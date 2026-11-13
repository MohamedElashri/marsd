import { useEffect, useState, type ReactNode } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { Brand, Icon } from './ui';
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
  const [create, setCreate] = useState(false);
  const [project, setProject] = useState('');
  const [roots, setRoots] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!nativeDesktop) return;
    const refresh = () =>
      nativeInvoke<{ running: boolean }>('desktop_status')
        .then((state) => {
          setReady(state.running);
          if (!state.running) {
            setCreate(false);
            setProject('');
            setRoots([]);
          }
        })
        .catch((reason) => {
          setError(String(reason));
          setReady(false);
        });
    void refresh();
    window.addEventListener('marsd-project-changed', refresh);
    return () => window.removeEventListener('marsd-project-changed', refresh);
  }, []);
  if (!nativeDesktop || ready) return children;
  async function open(path: string, make: boolean) {
    setBusy(true);
    setError('');
    try {
      await nativeInvoke('open_project', {
        project: path,
        create: make,
        roots: make ? [path, ...roots] : [],
      });
      setReady(true);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  async function chooseProject() {
    setError('');
    setBusy(true);
    try {
      const path = await pickNativePath(true);
      if (path) {
        if (create) setProject(path);
        else await open(path, false);
      }
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="native-launcher">
      <Brand />
      <section className="launcher-content">
        <h1>{create ? 'Create a project' : 'Open a local project'}</h1>
        <p>
          {create
            ? 'Choose a workspace folder for metadata and reports. Existing source files stay in their original locations.'
            : 'Continue a local scientific investigation, or create a workspace for your files.'}
        </p>
        {ready === null ? (
          <p role="status">Checking local runtime…</p>
        ) : !create ? (
          <div className="launcher-actions">
            <button disabled={busy} onClick={() => void chooseProject()}>
              <Icon name="Files" />
              {busy ? 'Opening…' : 'Open project…'}
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => {
                setCreate(true);
                setError('');
              }}
            >
              <Icon name="Plus" />
              Create project…
            </button>
          </div>
        ) : (
          <>
            <div className="folder-choice">
              <label>
                Project folder
                <input value={project} readOnly placeholder="Choose a local workspace folder" />
              </label>
              <button className="secondary" disabled={busy} onClick={() => void chooseProject()}>
                Choose folder
              </button>
            </div>
            <details className="native-roots">
              <summary>Additional source folders ({roots.length})</summary>
              <p>
                The project folder is approved by default. Add other local folders this project may
                inspect.
              </p>
              <ul>
                {roots.map((root) => (
                  <li key={root}>
                    <code>{root}</code>
                    <button
                      className="secondary"
                      disabled={busy}
                      aria-label={`Remove source folder ${root}`}
                      onClick={() => setRoots((old) => old.filter((value) => value !== root))}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
              <button
                className="secondary"
                disabled={busy}
                onClick={async () => {
                  try {
                    const path = await pickNativePath(true);
                    if (path) setRoots((old) => Array.from(new Set([...old, path])));
                  } catch (reason) {
                    setError(String(reason));
                  }
                }}
              >
                Add source folder
              </button>
            </details>
            <div className="dialog-actions">
              <button
                className="secondary"
                disabled={busy}
                onClick={() => {
                  setCreate(false);
                  setProject('');
                  setRoots([]);
                  setError('');
                }}
              >
                Back
              </button>
              <button disabled={busy || !project} onClick={() => void open(project, true)}>
                {busy ? 'Creating…' : 'Create project'}
              </button>
            </div>
          </>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <p className="scope-note">
          Local disk projects only. One writer per project. Desktop distribution remains
          experimental.
        </p>
      </section>
      <footer className="launcher-footer">
        Bundled Python runtime · no account · offline after installation
      </footer>
    </main>
  );
}
