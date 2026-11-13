#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod backend;
use backend::{Backend, Reply};
use serde::Serialize;
use serde_json::Value;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Mutex,
};
use tauri::{Manager, State};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};

#[derive(Default)]
struct DesktopState {
    backend: Mutex<Option<Backend>>,
    exiting: AtomicBool,
    approved: AtomicBool,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Status {
    running: bool,
    project: Option<String>,
}

#[tauri::command]
fn desktop_status(state: State<DesktopState>) -> Result<Status, String> {
    let mut guard = state
        .backend
        .lock()
        .map_err(|_| "Native state is unavailable")?;
    let running = guard
        .as_mut()
        .is_some_and(|backend| matches!(backend.child.try_wait(), Ok(None)));
    Ok(Status {
        running,
        project: guard.as_ref().map(|backend| backend.project.clone()),
    })
}
#[tauri::command]
async fn pick_path(app: tauri::AppHandle, directory: bool) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let dialog = app.dialog().file();
        let result = if directory {
            dialog.blocking_pick_folder()
        } else {
            dialog.blocking_pick_file()
        };
        match result {
            Some(path) => {
                let path = path
                    .into_path()
                    .map_err(|_| "Select a local filesystem path.")?;
                Ok(Some(
                    path.to_str()
                        .ok_or("Select a path with valid Unicode characters.")?
                        .to_owned(),
                ))
            }
            None => Ok(None),
        }
    })
    .await
    .map_err(|_| "Native dialog failed")?
}
#[tauri::command]
async fn open_project(
    app: tauri::AppHandle,
    project: String,
    create: bool,
    roots: Vec<String>,
) -> Result<(), String> {
    if project.len() > 8192 || roots.len() > 128 {
        return Err("Project selection exceeds its limit".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<DesktopState>();
        let mut guard = state
            .backend
            .lock()
            .map_err(|_| "Native state is unavailable")?;
        if guard
            .as_mut()
            .is_some_and(|backend| matches!(backend.child.try_wait(), Ok(None)))
        {
            return Err("Close the current project first.".into());
        }
        guard.take();
        let executable = app
            .path()
            .resource_dir()
            .map_err(|_| "Application resources are missing")?
            .join("runtime/backend")
            .join(if cfg!(windows) {
                "marsd-backend.exe"
            } else {
                "marsd-backend"
            });
        *guard = Some(Backend::start(&executable, &project, create, &roots)?);
        Ok(())
    })
    .await
    .map_err(|_| "Project startup failed")?
}
#[tauri::command]
async fn backend_request(
    state: State<'_, DesktopState>,
    path: String,
    method: String,
    body: Option<Value>,
) -> Result<Reply, String> {
    let connection = state
        .backend
        .lock()
        .map_err(|_| "Native state is unavailable")?
        .as_ref()
        .ok_or("Open a project first")?
        .connection
        .clone();
    tauri::async_runtime::spawn_blocking(move || {
        backend::request(&connection, &method, &path, body)
    })
    .await
    .map_err(|_| "Native request failed")?
}
fn close(app: &tauri::AppHandle) -> bool {
    let state = app.state::<DesktopState>();
    let mut guard = match state.backend.lock() {
        Ok(guard) => guard,
        Err(_) => return false,
    };
    if guard.as_ref().is_some_and(Backend::active_work) {
        let mut dialog = app
            .dialog()
            .message(
                "An operation may still be running. Cancel active work and close this project?",
            )
            .title("Close Marsd project")
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Cancel work and close".into(),
                "Keep open".into(),
            ));
        if let Some(window) = app.get_webview_window("main") {
            dialog = dialog.parent(&window);
        }
        if !dialog.blocking_show() {
            return false;
        }
    }
    guard.take();
    true
}
#[tauri::command]
async fn close_project(app: tauri::AppHandle) -> Result<bool, String> {
    tauri::async_runtime::spawn_blocking(move || close(&app))
        .await
        .map_err(|_| "Project close failed".into())
}
fn request_exit(app: &tauri::AppHandle) {
    let state = app.state::<DesktopState>();
    if state.exiting.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if close(&app) {
            app.state::<DesktopState>()
                .approved
                .store(true, Ordering::SeqCst);
            app.exit(0);
        } else {
            app.state::<DesktopState>()
                .exiting
                .store(false, Ordering::SeqCst);
        }
    });
}
fn local_navigation(url: &tauri::Url) -> bool {
    url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && ((url.scheme() == "tauri" && url.host_str() == Some("localhost"))
            || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost")))
}
fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(DesktopState::default())
        .invoke_handler(tauri::generate_handler![
            desktop_status,
            pick_path,
            open_project,
            backend_request,
            close_project
        ])
        .setup(|app| {
            // Configuration creates the only window. Reject navigation before loading any project data.
            let config = app
                .config()
                .app
                .windows
                .first()
                .ok_or("Window configuration is missing")?;
            let window = tauri::WebviewWindowBuilder::from_config(app, config)?
                .on_navigation(local_navigation)
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .build()?;
            let app_handle = app.handle().clone();
            window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    if !app_handle
                        .state::<DesktopState>()
                        .approved
                        .load(Ordering::SeqCst)
                    {
                        api.prevent_close();
                        request_exit(&app_handle);
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Could not initialize the Marsd native shell");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event {
            if !app.state::<DesktopState>().approved.load(Ordering::SeqCst) {
                api.prevent_exit();
                request_exit(app);
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn window_rejects_remote_and_file_navigation() {
        for url in [
            "https://example.com",
            "file:///etc/passwd",
            "http://127.0.0.1:8000",
            "tauri://attacker",
            "http://tauri.localhost.attacker",
            "http://tauri.localhost:8080/",
            "tauri://user@localhost/",
        ] {
            assert!(!local_navigation(&tauri::Url::parse(url).unwrap()));
        }
        assert!(local_navigation(
            &tauri::Url::parse("tauri://localhost/").unwrap()
        ));
        assert!(local_navigation(
            &tauri::Url::parse("http://tauri.localhost/").unwrap()
        ));
    }
}
