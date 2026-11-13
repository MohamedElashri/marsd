use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Read, Write},
    path::Path,
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

const MAX_BODY: usize = 1024 * 1024;
const MAX_RESPONSE: u64 = 8 * 1024 * 1024;

#[derive(Clone)]
pub struct Connection {
    pub port: u16,
    token: String,
}
#[derive(Serialize)]
pub struct Reply {
    pub status: u16,
    pub data: Value,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Ready {
    protocol: u8,
    pid: u32,
    port: u16,
    code: String,
}

pub fn client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(35))
        .connect_timeout(Duration::from_secs(2))
        .build()
        .map_err(|_| "Could not initialize the local transport.".into())
}

pub fn request(
    connection: &Connection,
    method: &str,
    path: &str,
    body: Option<Value>,
) -> Result<Reply, String> {
    if path.len() > 8192 || !["GET", "POST", "DELETE"].contains(&method) {
        return Err("Request exceeds the desktop transport scope.".into());
    }
    let base = format!("http://127.0.0.1:{}/", connection.port);
    let url = reqwest::Url::parse(&format!("{base}api/"))
        .map_err(|_| "Invalid local endpoint")?
        .join(path)
        .map_err(|_| "Invalid API path")?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port() != Some(connection.port)
        || !url.path().starts_with("/api/")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || url.path() == "/api/session"
    {
        return Err("Request is outside the local API scope.".into());
    }
    let mut builder = client()?
        .request(
            reqwest::Method::from_bytes(method.as_bytes()).map_err(|_| "Invalid HTTP method")?,
            url,
        )
        .header("Origin", base.trim_end_matches('/'))
        .bearer_auth(&connection.token);
    if let Some(value) = body {
        let bytes = serde_json::to_vec(&value).map_err(|_| "Invalid request JSON")?;
        if bytes.len() > MAX_BODY {
            return Err("Request exceeds 1 MiB.".into());
        }
        builder = builder
            .header("Content-Type", "application/json")
            .body(bytes);
    }
    let response = builder
        .send()
        .map_err(|_| "The project backend is unavailable. Close and reopen the project.")?;
    let status = response.status().as_u16();
    let mut bytes = Vec::new();
    response
        .take(MAX_RESPONSE + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Could not read the backend response")?;
    if bytes.len() as u64 > MAX_RESPONSE {
        return Err("Response exceeds 8 MiB.".into());
    }
    let data =
        serde_json::from_slice(&bytes).map_err(|_| "The backend returned an invalid response")?;
    Ok(Reply { status, data })
}

pub struct Backend {
    pub child: Child,
    input: Option<ChildStdin>,
    stopped: bool,
    pub connection: Connection,
    pub project: String,
    #[cfg(windows)]
    job: Option<WindowsJob>,
}
impl Backend {
    pub fn start(
        executable: &Path,
        project: &str,
        create: bool,
        roots: &[String],
    ) -> Result<Self, String> {
        let transport = client()?;
        let mut command = Command::new(executable);
        command
            .arg("--desktop-serve")
            .arg("--project")
            .arg(project)
            .arg("--owner-pid")
            .arg(std::process::id().to_string())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        command.current_dir(executable.parent().ok_or("Bundled runtime is missing")?);
        for key in [
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONSTARTUP",
            "PYTHONINSPECT",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
        ] {
            command.env_remove(key);
        }
        if create {
            command.arg("--create");
            for root in roots {
                command.arg("--source-root").arg(root);
            }
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000 | 0x00000200);
        }
        let mut child=command.spawn().map_err(|_|"Bundled runtime could not start. Reinstall this platform's complete application bundle.")?;
        #[cfg(windows)]
        let job = match WindowsJob::attach(child.id()) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        let stdout = child
            .stdout
            .take()
            .ok_or("Runtime readiness pipe is missing")?;
        let (send, receive) = mpsc::channel();
        thread::spawn(move || {
            let mut line = Vec::new();
            let result = BufReader::new(stdout)
                .take(16 * 1024 + 1)
                .read_until(b'\n', &mut line)
                .map(|_| line);
            let _ = send.send(result);
        });
        let handshake = receive.recv_timeout(Duration::from_secs(40));
        let parsed: Result<Ready,String> = match handshake {
            Ok(Ok(bytes)) if bytes.len()<=16*1024 => {
                let value:Value=serde_json::from_slice(&bytes).map_err(|_|"Runtime readiness was invalid".to_string()).unwrap_or(Value::Null);
                if let Some(error)=value.get("error") { Err(format!("{}: {}",error.get("code").and_then(Value::as_str).unwrap_or("BACKEND_START_FAILED"),error.get("message").and_then(Value::as_str).unwrap_or("Open a valid local project or run the bundled diagnostics."))) }
                else { serde_json::from_value(value).map_err(|_|"Runtime did not report readiness. Run its --desktop-self-test diagnostics.".into()) }
            },
            _=>Err("Runtime startup timed out or failed. Run the bundled --desktop-self-test diagnostics.".into())
        };
        let ready = match parsed {
            Ok(ready)
                if ready.protocol == 1
                    && ready.pid == child.id()
                    && ready.port > 0
                    && (32..=256).contains(&ready.code.len()) =>
            {
                ready
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(parsed.err().unwrap_or("Runtime protocol mismatch".into()));
            }
        };
        let base = format!("http://127.0.0.1:{}", ready.port);
        let login = transport
            .post(format!("{base}/api/session"))
            .header("Origin", &base)
            .json(&json!({"code":ready.code}))
            .send();
        let token = login
            .ok()
            .filter(|response| response.status().is_success())
            .and_then(|response| response.json::<Value>().ok())
            .and_then(|value| {
                value
                    .get("access_token")
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            });
        let token = match token {
            Some(token) if (32..=256).contains(&token.len()) => token,
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("Native session authorization failed.".into());
            }
        };
        let input = child.stdin.take();
        Ok(Self {
            child,
            input,
            stopped: false,
            connection: Connection {
                port: ready.port,
                token,
            },
            project: project.into(),
            #[cfg(windows)]
            job: Some(job),
        })
    }
    pub fn active_work(&self) -> bool {
        let url = format!("http://127.0.0.1:{}/api/jobs", self.connection.port);
        client()
            .ok()
            .and_then(|client| {
                client
                    .get(url)
                    .bearer_auth(&self.connection.token)
                    .timeout(Duration::from_secs(2))
                    .send()
                    .ok()
            })
            .and_then(|response| response.json::<Value>().ok())
            .and_then(|value| value.as_array().cloned())
            .map(|jobs| {
                jobs.iter().any(|job| {
                    ["QUEUED", "RUNNING"].contains(&job["state"].as_str().unwrap_or("UNKNOWN"))
                })
            })
            .unwrap_or(true)
    }
    pub fn stop(&mut self) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        if let Some(mut input) = self.input.take() {
            let _ = input.write_all(b"shutdown\n");
        }
        let deadline = Instant::now() + Duration::from_secs(7);
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                break;
            }
            thread::sleep(Duration::from_millis(25));
        }
        // Only signal a group while its leader is still our unreaped child. A previously
        // reaped PID could have been reused while a failed project stayed in the UI.
        if matches!(self.child.try_wait(), Ok(None)) {
            #[cfg(unix)]
            unsafe {
                libc::kill(-(self.child.id() as i32), libc::SIGTERM);
            }
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
        #[cfg(windows)]
        {
            self.job.take();
        }
    }
}
impl Drop for Backend {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(windows)]
struct WindowsJob(isize);
#[cfg(windows)]
impl WindowsJob {
    fn attach(pid: u32) -> Result<Self, String> {
        use windows_sys::Win32::{
            Foundation::CloseHandle,
            System::{JobObjects::*, Threading::*},
        };
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err("Cannot create the backend process container".into());
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                std::mem::size_of_val(&limits) as u32,
            ) == 0
            {
                CloseHandle(handle);
                return Err("Cannot configure process cleanup".into());
            }
            let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false as i32, pid);
            if process.is_null() || AssignProcessToJobObject(handle, process) == 0 {
                if !process.is_null() {
                    CloseHandle(process);
                }
                CloseHandle(handle);
                return Err("Cannot attach the backend process container".into());
            }
            CloseHandle(process);
            Ok(Self(handle as isize))
        }
    }
}
#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0 as _);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn transport_refuses_remote_paths_and_methods() {
        let connection = Connection {
            port: 1,
            token: "test-token".into(),
        };
        for path in [
            "https://example.com/data",
            "../health",
            "//example.com/api/jobs",
            "session",
        ] {
            assert!(request(&connection, "GET", path, None).is_err());
        }
        assert!(request(&connection, "PUT", "jobs", None).is_err());
        assert!(request(
            &connection,
            "POST",
            "jobs",
            Some(json!({"large":"x".repeat(MAX_BODY+1)}))
        )
        .is_err());
    }
    #[test]
    #[ignore = "requires MARSD_TEST_BACKEND pointing to the native frozen runtime"]
    fn frozen_backend_lifecycle_and_proxy() {
        let executable = std::path::PathBuf::from(
            std::env::var("MARSD_TEST_BACKEND").expect("Set MARSD_TEST_BACKEND"),
        );
        let root = std::env::temp_dir().join(format!(
            "marsd-rust-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        let project = root.to_str().unwrap();
        {
            let mut backend = Backend::start(&executable, project, true, &[]).unwrap();
            assert_eq!(
                request(&backend.connection, "GET", "project", None)
                    .unwrap()
                    .status,
                200
            );
            assert!(!backend.active_work());
            assert!(Backend::start(&executable, project, false, &[]).is_err());
            assert!(request(&backend.connection, "GET", "http://example.com/", None).is_err());
            backend.stop();
            backend.stop();
            assert!(backend.child.try_wait().unwrap().is_some());
        }
        {
            let mut backend = Backend::start(&executable, project, false, &[]).unwrap();
            assert_eq!(
                request(&backend.connection, "GET", "jobs", None)
                    .unwrap()
                    .status,
                200
            );
            backend.child.kill().unwrap();
            backend.child.wait().unwrap();
            assert!(
                backend.active_work(),
                "Backend failure must require a close decision"
            );
        }
        assert!(root.join(".marsd").is_dir());
        std::fs::remove_dir_all(root).unwrap();
    }
}
