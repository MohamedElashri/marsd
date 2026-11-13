import { useEffect, useState, type ReactNode } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';

export const nativeDesktop = isTauri();
export function nativeInvoke<T>(command: string, args?: Record<string, unknown>) {
  return invoke<T>(command, args);
}
export async function pickNativePath(directory: boolean) {
  return nativeInvoke<string | null>('pick_path', { directory });
}
export async function closeNativeProject() {
  if (await nativeInvoke<boolean>('close_project'))
    window.dispatchEvent(new Event('marsd-project-changed'));
}

export function DesktopRoot({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState<boolean | null>(null);
  const [project, setProject] = useState('');
  const [create, setCreate] = useState(false);
  const [roots, setRoots] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!nativeDesktop) return;
    const refresh = () =>
      nativeInvoke<{ running: boolean }>('desktop_status')
        .then((state) => setReady(state.running))
        .catch((reason) => {
          setError(String(reason));
          setReady(false);
        });
    void refresh();
    window.addEventListener('marsd-project-changed', refresh);
    return () => window.removeEventListener('marsd-project-changed', refresh);
  }, []);
  if (!nativeDesktop) return children;
  if (ready) return children;
  async function chooseProject() {
    try {
      const path = await pickNativePath(true);
      if (path) {
        setProject(path);
        setError('');
      }
    } catch (reason) {
      setError(String(reason));
    }
  }
  async function addRoot() {
    try {
      const path = await pickNativePath(true);
      if (path) setRoots((old) => Array.from(new Set([...old, path])));
    } catch (reason) {
      setError(String(reason));
    }
  }
  return (
    <main className="unlock native-launcher">
      <div className="brand">
        <svg viewBox="0 0 32 32" aria-hidden="true">
          <path d="M4 24V8h5l7 12 7-12h5v16" fill="none" stroke="currentColor" strokeWidth="2.5" />
          <path d="M4 29h24" stroke="currentColor" strokeWidth="2" />
        </svg>
        <span>
          Marsd <b lang="ar">مرصد</b>
        </span>
      </div>
      <div className="unlock-content">
        <span className="session-symbol" aria-hidden="true">
          ⌁
        </span>
        <h1>Open a local project.</h1>
        <p>
          Your data stays where it is. Inspections are read-only; project metadata and reports stay
          in the chosen workspace.
        </p>
        <div className="launcher-mode">
          <button
            className={!create ? '' : 'secondary'}
            disabled={busy}
            onClick={() => setCreate(false)}
          >
            Existing project
          </button>
          <button
            className={create ? '' : 'secondary'}
            disabled={busy}
            onClick={() => setCreate(true)}
          >
            New project
          </button>
        </div>
        <label>
          Project folder
          <input value={project} readOnly placeholder="Choose a local folder" />
        </label>
        <button className="secondary" disabled={busy} onClick={chooseProject}>
          Choose project folder
        </button>
        {create && (
          <div className="native-roots">
            <h2>Approved source folders</h2>
            <p>
              By default, files inside the project are approved. Add other local folders only if
              this project should be able to inspect them.
            </p>
            <ul>
              {roots.map((root) => (
                <li key={root}>
                  <code>{root}</code>
                  <button
                    className="secondary"
                    aria-label={`Remove source folder ${root}`}
                    onClick={() => setRoots((old) => old.filter((item) => item !== root))}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
            <button className="secondary" disabled={busy} onClick={addRoot}>
              Add source folder
            </button>
          </div>
        )}
        <p className="muted">
          {create
            ? 'Creates .marsd metadata without copying or replacing source data.'
            : 'Select a folder that already contains .marsd project metadata.'}
        </p>
        <button
          disabled={busy || !project || ready === null}
          onClick={async () => {
            setBusy(true);
            setError('');
            try {
              await nativeInvoke('open_project', {
                project,
                create,
                roots: create ? [project, ...roots] : [],
              });
              setReady(true);
            } catch (reason) {
              setError(String(reason));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? 'Opening project…' : create ? 'Create project' : 'Open project'}
        </button>
        {error && (
          <p className="error banner" role="alert">
            {error}
          </p>
        )}
        <p className="muted">
          Experimental desktop. Use a local disk and keep source files backed up.
        </p>
      </div>
      <footer>Bundled runtime · offline after installation · no account</footer>
    </main>
  );
}
