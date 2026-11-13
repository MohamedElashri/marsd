import React, { useEffect, useRef, useState, type PointerEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { api, authenticated, forgetSession, login, type Data } from './api';
import { VERSION, type Request } from './schema';
import { DesktopRoot, nativeDesktop, closeNativeProject, pickNativePath } from './desktop';
import { Runs, Lineage } from './evidence';
import { ResultView } from './results';
import { Brand, Dialog, Empty, FileType, Icon, Search, Badge } from './ui';
import {
  Navigator,
  FileOverview,
  DataView,
  CompareView,
  ValidateView,
  Inspector,
  initialControls,
  type Document,
  type Controls,
  type Operation,
} from './workbench';
import './style.css';

type View = 'Workbench' | 'Runs' | 'Lineage' | 'Reports';
type Task = 'Data' | 'Compare' | 'Validate';
type OpenDocument = Document & { task: Task };
type Context = { version: string; revision: number; controls: Controls };
type Queued = { request: Request; context: Context };
const finished = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'INTERRUPTED']);
const operations: Record<string, string> = {
  inspect: 'File structure',
  preview: 'Preview',
  profile: 'Statistics',
  diff: 'Comparison',
  validate: 'Validation',
};
const OPEN_DOCUMENT_LIMIT = 8;

function Divider({
  label,
  value,
  change,
  min,
  max,
  reversed = false,
}: {
  label: string;
  value: number;
  change: (value: number) => void;
  min: number;
  max: number;
  reversed?: boolean;
}) {
  const start = useRef<{ x: number; value: number } | null>(null);
  function move(event: PointerEvent) {
    if (start.current)
      change(
        Math.max(
          min,
          Math.min(
            max,
            start.current.value + (event.clientX - start.current.x) * (reversed ? -1 : 1),
          ),
        ),
      );
  }
  return (
    <div
      className={`pane-divider ${reversed ? 'inspector-divider' : 'navigator-divider'}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      tabIndex={0}
      onPointerDown={(event) => {
        start.current = { x: event.clientX, value };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={move}
      onPointerUp={() => {
        start.current = null;
      }}
      onLostPointerCapture={() => {
        start.current = null;
      }}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          change(
            Math.max(
              min,
              Math.min(max, value + (event.key === 'ArrowRight' ? 16 : -16) * (reversed ? -1 : 1)),
            ),
          );
        }
      }}
    />
  );
}
function App() {
  const [unlocked, setUnlocked] = useState(authenticated);
  const sessionEpoch = useRef(0);
  const [code, setCode] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [project, setProject] = useState<Data | null>(null);
  const [artifacts, setArtifacts] = useState<Data[]>([]);
  const [contracts, setContracts] = useState<Data[]>([]);
  const [reports, setReports] = useState<Data[]>([]);
  const [jobs, setJobs] = useState<Data[]>([]);
  const [documents, setDocuments] = useState<OpenDocument[]>([]);
  const [active, setActive] = useState('');
  const documentsRef = useRef(documents);
  documentsRef.current = documents;
  const activeRef = useRef(active);
  activeRef.current = active;
  const [view, setView] = useState<View>('Workbench');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [job, setJob] = useState<Data | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const submissionRef = useRef<{ cancelled: boolean } | null>(null);
  const busyRef = useRef(false);
  const pending = useRef<Queued | null>(null);
  const [navigatorWidth, setNavigatorWidth] = useState(248);
  const [inspectorWidth, setInspectorWidth] = useState(244);
  const [showInspector, setShowInspector] = useState(
    () => window.matchMedia('(min-width: 1151px)').matches,
  );
  const [showNavigator, setShowNavigator] = useState(
    () => window.matchMedia('(min-width: 721px)').matches,
  );
  const navigatorToggle = useRef<HTMLButtonElement>(null);
  const inspectorToggle = useRef<HTMLButtonElement>(null);
  function hideNavigator() {
    setShowNavigator(false);
    requestAnimationFrame(() => navigatorToggle.current?.focus());
  }
  function hideInspector() {
    setShowInspector(false);
    requestAnimationFrame(() => inspectorToggle.current?.focus());
  }
  function toggleNavigator() {
    if (showNavigator) hideNavigator();
    else {
      if (window.matchMedia('(max-width: 720px)').matches) setShowInspector(false);
      setShowNavigator(true);
    }
  }
  function toggleInspector() {
    if (showInspector) hideInspector();
    else {
      if (window.matchMedia('(max-width: 720px)').matches) setShowNavigator(false);
      setShowInspector(true);
    }
  }
  useEffect(() => {
    const narrow = window.matchMedia('(max-width: 1150px)');
    const phone = window.matchMedia('(max-width: 720px)');
    const closeInspector = () => {
      if (narrow.matches) setShowInspector(false);
    };
    const closeNavigator = () => {
      if (phone.matches) setShowNavigator(false);
    };
    narrow.addEventListener('change', closeInspector);
    phone.addEventListener('change', closeNavigator);
    return () => {
      narrow.removeEventListener('change', closeInspector);
      phone.removeEventListener('change', closeNavigator);
    };
  }, []);
  const [dialog, setDialog] = useState<'add' | 'project' | 'help' | 'jobs' | null>(null);
  const [path, setPath] = useState('');
  const [dialogError, setDialogError] = useState('');
  const [adding, setAdding] = useState(false);
  const [runVersion, setRunVersion] = useState<string | undefined>();
  const [focusedRun, setFocusedRun] = useState('');
  const [graphFocus, setGraphFocus] = useState('');
  const [selectedReport, setSelectedReport] = useState<Data | null>(null);
  const [reportQuery, setReportQuery] = useState('');
  const [reportKind, setReportKind] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const reportSequence = useRef(0);
  const [reportLoading, setReportLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const refreshSequence = useRef(0);
  const document = documents.find((item) => item.source.version_id === active);
  const busy = submitting || Boolean(job && !finished.has(job.state));

  useEffect(() => {
    if (view !== 'Workbench' || !active) return;
    const tab = globalThis.document.getElementById(`file-tab-${active}`);
    const list = tab?.closest('.document-tabs');
    if (!tab || !list) return;
    const reveal = () => tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const observer = new ResizeObserver(reveal);
    observer.observe(list);
    reveal();
    return () => observer.disconnect();
  }, [active, view]);
  function updateDocument(version: string, update: (document: OpenDocument) => OpenDocument) {
    setDocuments((old) =>
      old.map((item) => (item.source.version_id === version ? update(item) : item)),
    );
  }
  async function refresh() {
    const sequence = ++refreshSequence.current;
    const [p, a, c, r, j] = await Promise.all([
      api('project'),
      api<Data[]>('artifacts'),
      api<Data[]>('contracts'),
      api<Data[]>('reports'),
      api<Data[]>('jobs'),
    ]);
    const historical = documentsRef.current.filter((item) =>
      a.some((file) => file.id === item.source.id && file.version_id !== item.source.version_id),
    );
    const checked = await Promise.allSettled(
      historical.map((item) => api(`versions/${encodeURIComponent(item.source.version_id)}`)),
    );
    const availability = new Map(
      historical.map((item, index) => [
        item.source.version_id,
        checked[index].status === 'fulfilled'
          ? (checked[index] as PromiseFulfilledResult<Data>).value
          : { stale: true },
      ]),
    );
    if (sequence !== refreshSequence.current) return;
    setProject(p);
    setArtifacts(a);
    setContracts(c);
    setReports(r);
    setJobs(j);
    setDocuments((old) =>
      old.map((item) => {
        const current = a.find((file) => file.version_id === item.source.version_id);
        const recorded = availability.get(item.source.version_id);
        return current
          ? { ...item, source: { ...item.source, stale: current.stale } }
          : recorded
            ? {
                ...item,
                source: {
                  ...item.source,
                  stale: recorded.stale,
                  current_path: recorded.current_path || item.source.current_path,
                },
              }
            : item;
      }),
    );
    const running = j.find((item) => !finished.has(item.state));
    if (running && !busyRef.current) {
      busyRef.current = true;
      setJob({ ...running, context: null });
    }
  }
  useEffect(() => {
    if (unlocked) void refresh().catch((reason) => setError(reason.message));
  }, [unlocked]);
  function requestFor(
    document: OpenDocument,
    operation: Operation,
    patch: Partial<Request> = {},
  ): Queued {
    const controls = document.controls;
    return {
      context: {
        version: document.source.version_id,
        revision: document.revision,
        controls: { ...controls },
      },
      request: {
        operation,
        path: document.source.current_path || document.source.path,
        version_id: document.source.version_id,
        node: controls.node,
        selection: controls.selection,
        columns: controls.columns
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
        offset: controls.offset,
        limit: 64,
        max_rows: controls.maxRows,
        ...patch,
      },
    };
  }
  async function start(next: Queued) {
    const session = sessionEpoch.current;
    const submission = { cancelled: false };
    submissionRef.current = submission;
    busyRef.current = true;
    setSubmitting(true);
    setCancelling(false);
    setError('');
    setNotice('');
    try {
      const reply = await api('jobs', next.request);
      if (session !== sessionEpoch.current) return;
      setJob({ id: reply.id, state: 'QUEUED', request: next.request, context: next.context });
      if (submission.cancelled) {
        try {
          await api(`jobs/${reply.id}`, undefined, 'DELETE');
        } catch (reason) {
          if (session === sessionEpoch.current) {
            setError((reason as Error).message);
            setCancelling(false);
          }
        }
      }
    } catch (reason) {
      if (session !== sessionEpoch.current) return;
      busyRef.current = false;
      setCancelling(false);
      setError((reason as Error).message);
      const queued = pending.current;
      pending.current = null;
      if (queued) void start(queued);
    } finally {
      if (submissionRef.current === submission) {
        submissionRef.current = null;
        if (session === sessionEpoch.current) setSubmitting(false);
      }
    }
  }
  function schedule(document: OpenDocument, operation: Operation, patch: Partial<Request> = {}) {
    if (document.source.stale) {
      setError(
        'This recorded source changed or is unavailable. Add the current file deliberately to create a new observation.',
      );
      return;
    }
    const next = requestFor(document, operation, patch);
    if (busyRef.current) pending.current = next;
    else void start(next);
  }
  const complete = useRef<(job: Data, context: Context | null) => void>(() => {});
  complete.current = (value, context) => {
    busyRef.current = false;
    setCancelling(false);
    const current =
      context &&
      documentsRef.current.find(
        (item) => item.source.version_id === context.version && item.revision === context.revision,
      );
    let preview: Queued | null = null;
    if (value.state === 'SUCCEEDED' && current && value.result) {
      const result = value.result;
      if (result.kind === 'inspect') {
        const candidate =
          result.nodes?.find(
            (node: Data) =>
              node.path === current.controls.node && node.supported && node.shape?.length <= 2,
          ) ||
          result.nodes?.find((node: Data) => node.supported && node.shape?.length === 2) ||
          result.nodes?.find((node: Data) => node.supported && node.shape?.length <= 2);
        const inspected = {
          ...current,
          metadata: result,
          controls: { ...current.controls, node: candidate?.path || '/' },
        };
        updateDocument(current.source.version_id, () => inspected);
        if (
          activeRef.current === current.source.version_id &&
          (current.source.format !== 'HDF5' || candidate)
        )
          preview = requestFor(inspected, 'preview');
      } else
        updateDocument(current.source.version_id, (old) => ({
          ...old,
          results: { ...old.results, [result.kind]: result },
          appliedControls: { ...old.appliedControls, [result.kind]: context!.controls },
        }));
    } else if (value.state !== 'SUCCEEDED' && value.state !== 'CANCELLED' && (!context || current))
      setError(
        `${value.error?.code || value.state}: ${value.error?.message || 'The operation did not complete.'} ${value.error?.suggested_action || ''}`,
      );
    const next = pending.current || preview;
    pending.current = null;
    if (
      next &&
      documentsRef.current.some((item) => item.source.version_id === next.context.version)
    )
      void start(next);
    void refresh().catch((reason) => setError(reason.message));
  };
  useEffect(() => {
    if (!unlocked || !job || finished.has(job.state)) return;
    let stopped = false;
    let timer: number;
    const currentJob = job;
    async function poll() {
      try {
        const value = await api(`jobs/${currentJob.id}`);
        if (stopped) return;
        setJob({ ...value, context: currentJob.context });
        if (finished.has(value.state)) complete.current(value, currentJob.context);
        else timer = window.setTimeout(poll, 180);
      } catch (reason) {
        if (!stopped) {
          setError((reason as Error).message);
          timer = window.setTimeout(poll, 1000);
        }
      }
    }
    timer = window.setTimeout(poll, 120);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [job?.id, unlocked]);
  function openDocument(source: Data) {
    const existing = documentsRef.current.find(
      (item) => item.source.version_id === source.version_id,
    );
    if (!existing && documentsRef.current.length >= OPEN_DOCUMENT_LIMIT) {
      setNotice(
        `Close a document before opening another. The workbench keeps up to ${OPEN_DOCUMENT_LIMIT} bounded document previews in memory.`,
      );
      return;
    }
    const next: OpenDocument = existing
      ? { ...existing, source: { ...existing.source, ...source } }
      : {
          source,
          controls: initialControls(contracts.find((item) => !item.invalid)?.path || ''),
          metadata: null,
          results: {},
          appliedControls: {},
          revision: 0,
          task: 'Data',
        };
    if (existing) updateDocument(source.version_id, () => next);
    else setDocuments((old) => [...old, next]);
    activeRef.current = source.version_id;
    setActive(source.version_id);
    setView('Workbench');
    setError('');
    setNotice('');
    if (!next.source.stale && !next.metadata)
      schedule(next, 'inspect', { node: '/', selection: '', columns: [], offset: 0 });
    else if (!next.source.stale && next.metadata && !next.results.preview && next.task === 'Data')
      schedule(next, 'preview');
  }
  function closeDocument(version: string) {
    const index = documentsRef.current.findIndex((item) => item.source.version_id === version);
    const remaining = documentsRef.current.filter((item) => item.source.version_id !== version);
    setDocuments(remaining);
    if (pending.current?.context.version === version) pending.current = null;
    if (activeRef.current === version) {
      const next = remaining[Math.min(index, remaining.length - 1)];
      activeRef.current = next?.source.version_id || '';
      setActive(activeRef.current);
      if (next && !next.metadata && !next.source.stale) schedule(next, 'inspect', { node: '/' });
    }
    requestAnimationFrame(() => {
      const tab = globalThis.document.getElementById(`file-tab-${activeRef.current}`);
      (tab || navigatorToggle.current)?.focus();
    });
  }
  function changeControls(patch: Partial<Controls>) {
    if (!document) return;
    updateDocument(document.source.version_id, (old) => ({
      ...old,
      controls: { ...old.controls, ...patch },
      ...(patch.node !== undefined && patch.node !== old.controls.node
        ? { revision: old.revision + 1, results: {}, appliedControls: {} }
        : {}),
    }));
  }
  function chooseNode(path: string) {
    if (!document) return;
    const next = {
      ...document,
      controls: {
        ...document.controls,
        node: path,
        selection: '',
        offset: 0,
        dataMode: 'preview' as const,
        arrayView: 'plot' as const,
      },
      results: {},
      appliedControls: {},
      revision: document.revision + 1,
      task: 'Data' as const,
    };
    updateDocument(document.source.version_id, () => next);
    schedule(next, 'preview');
  }
  async function openVersion(
    version: string,
    destination: 'Explore' | 'Validate' | 'Runs' | 'Lineage',
  ) {
    const session = sessionEpoch.current;
    try {
      const source = await api(`versions/${encodeURIComponent(version)}`);
      if (session !== sessionEpoch.current) return;
      if (destination === 'Runs') {
        setRunVersion(version);
        setView('Runs');
        return;
      }
      if (destination === 'Lineage') {
        setGraphFocus(version);
        setView('Lineage');
        return;
      }
      openDocument(source);
      if (destination === 'Validate')
        updateDocument(version, (old) => ({ ...old, task: 'Validate' }));
      if (source.stale)
        setNotice(
          'Historical source values are unavailable. Saved evidence remains accessible; a different observation is never substituted.',
        );
    } catch (reason) {
      if (session === sessionEpoch.current) setError((reason as Error).message);
    }
  }
  async function cancel() {
    if (cancelling) return;
    pending.current = null;
    if (submissionRef.current) {
      submissionRef.current.cancelled = true;
      setCancelling(true);
      return;
    }
    if (!job || finished.has(job.state)) return;
    const session = sessionEpoch.current;
    setCancelling(true);
    try {
      await api(`jobs/${job.id}`, undefined, 'DELETE');
    } catch (reason) {
      if (session === sessionEpoch.current) {
        setError((reason as Error).message);
        setCancelling(false);
      }
    }
  }
  const keyboard = useRef<(event: KeyboardEvent) => void>(() => {});
  keyboard.current = (event) => {
    if (dialog || !unlocked) return;
    const target = event.target as HTMLElement;
    const editing =
      target.isContentEditable || ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName);
    if (event.key === 'Escape' && view === 'Workbench') {
      const clearingSearch =
        target instanceof HTMLInputElement && target.type === 'search' && target.value;
      if (!clearingSearch && showInspector && window.matchMedia('(max-width: 1150px)').matches) {
        event.preventDefault();
        hideInspector();
        return;
      }
      if (!clearingSearch && showNavigator && window.matchMedia('(max-width: 720px)').matches) {
        event.preventDefault();
        hideNavigator();
        return;
      }
    }
    if (event.altKey && !event.ctrlKey && !event.metaKey && !editing) {
      const index = Number(event.code.startsWith('Digit') ? event.code.slice(5) : event.key) - 1;
      if (index >= 0 && index < documents.length) {
        event.preventDefault();
        openDocument(documents[index].source);
      }
    }
    if (event.key === 'Escape' && busyRef.current && !editing) {
      event.preventDefault();
      void cancel();
    }
    if (
      (event.ctrlKey || event.metaKey) &&
      event.key === 'Enter' &&
      document &&
      !busyRef.current &&
      !editing &&
      view === 'Workbench' &&
      document.task === 'Data'
    ) {
      event.preventDefault();
      schedule(document, document.controls.dataMode);
    }
  };
  useEffect(() => {
    const handler = (event: KeyboardEvent) => keyboard.current(event);
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);
  function showDialog(value: typeof dialog) {
    setDialogError('');
    setDialog(value);
  }
  async function addFile(event: React.FormEvent) {
    event.preventDefault();
    setAdding(true);
    setDialogError('');
    try {
      const source = await api('artifacts', { path });
      await refresh();
      setPath('');
      setDialog(null);
      openDocument(source);
    } catch (reason) {
      setDialogError((reason as Error).message);
    } finally {
      setAdding(false);
    }
  }
  async function pickFile(contract = false) {
    if (!nativeDesktop) {
      if (contract)
        setNotice(
          'Enter a contract path inside an approved source folder. Browser sessions cannot open a native file chooser.',
        );
      return;
    }
    try {
      const value = await pickNativePath(false);
      if (value) {
        if (contract) changeControls({ contract: value });
        else setPath(value);
      }
    } catch (reason) {
      if (contract) setError(String(reason));
      else setDialogError(String(reason));
    }
  }
  async function loadReport(id: string) {
    const sequence = ++reportSequence.current;
    setReportLoading(true);
    setSelectedReport(null);
    setError('');
    try {
      const value = await api(`reports/${id}`);
      if (sequence === reportSequence.current) setSelectedReport({ ...value, report_id: id });
    } catch (reason) {
      if (sequence === reportSequence.current) setError((reason as Error).message);
    } finally {
      if (sequence === reportSequence.current) setReportLoading(false);
    }
  }
  async function exportReport(format: 'json' | 'html') {
    if (!selectedReport) return;
    const session = sessionEpoch.current;
    setExporting(true);
    try {
      const reply = await api(`reports/${selectedReport.report_id}/export`, { format, overwrite });
      if (session !== sessionEpoch.current) return;
      setNotice(`Saved ${reply.path}. Exports include bounded source examples.`);
    } catch (reason) {
      if (session === sessionEpoch.current) setError((reason as Error).message);
    } finally {
      if (session === sessionEpoch.current) setExporting(false);
    }
  }
  if (!unlocked)
    return (
      <main className="connection-screen">
        <Brand />
        <section className="connection-panel">
          <h1>Connect to your local workspace</h1>
          <p>
            Enter the one-use launch code printed by <code>marsd open</code>. This session connects
            only to your computer.
          </p>
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              setConnecting(true);
              setError('');
              try {
                await login(code.trim());
                setCode('');
                setUnlocked(true);
              } catch (reason) {
                setError((reason as Error).message);
              } finally {
                setConnecting(false);
              }
            }}
          >
            <label>
              Launch code
              <input
                type="password"
                autoComplete="off"
                autoFocus
                required
                value={code}
                onChange={(event) => setCode(event.target.value)}
              />
            </label>
            <button disabled={connecting}>{connecting ? 'Connecting…' : 'Open workspace'}</button>
          </form>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <details>
            <summary>Set up a project</summary>
            <p>
              <code>marsd project create PATH --source-root DATA_DIRECTORY</code> creates metadata
              without moving source files. Open it with <code>marsd open PATH</code>.
            </p>
          </details>
          <p className="scope-note">
            No account. No upload. Launch codes stay out of URLs and browser history.
          </p>
        </section>
      </main>
    );

  const visibleReports = reports.filter(
    (report) =>
      (!reportKind || report.kind === reportKind) &&
      `${report.kind} ${report.outcome || ''} ${report.inputs?.map((input: Data) => input.name || input.path).join(' ')}`
        .toLowerCase()
        .includes(reportQuery.toLowerCase()),
  );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace-content">
        Skip to workspace
      </a>
      <header className="app-header">
        <Brand />
        <span className="project-title" title={project?.path}>
          {project?.name || 'Opening project…'}
        </span>
        <nav aria-label="Project views">
          {(['Workbench', 'Runs', 'Lineage', 'Reports'] as View[]).map((item) => (
            <button
              key={item}
              aria-pressed={view === item}
              className={view === item ? 'active' : ''}
              onClick={() => {
                setView(item);
                if (item === 'Runs') {
                  setRunVersion(undefined);
                  setFocusedRun('');
                }
                setError('');
              }}
            >
              <Icon name={item === 'Workbench' ? 'Explore' : item} />
              <span>{item}</span>
            </button>
          ))}
        </nav>
        <div className="app-utilities">
          <button className="quiet" onClick={() => showDialog('project')}>
            <Icon name="Settings" />
            <span>Project</span>
          </button>
          <button
            className="icon-button"
            title="Help and keyboard shortcuts"
            aria-label="Help and keyboard shortcuts"
            onClick={() => showDialog('help')}
          >
            <Icon name="Help" />
          </button>
        </div>
      </header>
      {(error || notice) && (
        <div
          className={`message-bar ${error ? 'error' : 'notice'}`}
          role={error ? 'alert' : 'status'}
        >
          <Icon name="Info" />
          <span>{error || notice}</span>
          <button
            className="icon-button"
            aria-label="Dismiss message"
            onClick={() => {
              setError('');
              setNotice('');
            }}
          >
            <Icon name="Close" />
          </button>
        </div>
      )}
      <main id="workspace-content" tabIndex={-1} className="application-body">
        {view === 'Workbench' && (
          <div
            className={`workbench ${!showNavigator ? 'navigator-hidden' : ''} ${!showInspector || !document ? 'inspector-hidden' : ''}`}
            style={
              {
                '--navigator-width': `${navigatorWidth}px`,
                '--inspector-width': `${inspectorWidth}px`,
              } as React.CSSProperties
            }
          >
            {showNavigator && (
              <>
                <Navigator
                  key="navigator"
                  artifacts={artifacts}
                  document={document}
                  onOpen={openDocument}
                  onNode={chooseNode}
                  onAdd={() => showDialog('add')}
                  onRefresh={() => void refresh().catch((reason) => setError(reason.message))}
                  onHide={hideNavigator}
                />
                <Divider
                  label="Resize data navigator"
                  value={navigatorWidth}
                  change={setNavigatorWidth}
                  min={180}
                  max={420}
                />
              </>
            )}
            <div className="document-area">
              <div className="document-tabs-bar">
                <button
                  className="icon-button"
                  title={showNavigator ? 'Hide navigator' : 'Show navigator'}
                  aria-label={showNavigator ? 'Hide navigator' : 'Show navigator'}
                  aria-pressed={showNavigator}
                  ref={navigatorToggle}
                  onClick={toggleNavigator}
                >
                  <Icon name="Files" />
                </button>
                <div
                  className="document-tabs"
                  role="tablist"
                  aria-label="Open files"
                  onKeyDown={(event) => {
                    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
                    const target = event.target as HTMLElement;
                    if (target.getAttribute('role') !== 'tab') return;
                    event.preventDefault();
                    const index = documents.findIndex((item) => item.source.version_id === active);
                    const next =
                      documents[
                        (index + (event.key === 'ArrowRight' ? 1 : documents.length - 1)) %
                          documents.length
                      ];
                    if (next) {
                      openDocument(next.source);
                      window.requestAnimationFrame(() =>
                        globalThis.document
                          .getElementById(`file-tab-${next.source.version_id}`)
                          ?.focus(),
                      );
                    }
                  }}
                >
                  {documents.map((item) => (
                    <div
                      className={`document-tab ${active === item.source.version_id ? 'active' : ''}`}
                      key={item.source.version_id}
                    >
                      <button
                        id={`file-tab-${item.source.version_id}`}
                        role="tab"
                        aria-selected={active === item.source.version_id}
                        aria-controls="document-panel"
                        tabIndex={active === item.source.version_id ? 0 : -1}
                        title={`${item.source.path}\nVersion ${item.source.version_id}`}
                        onClick={() => openDocument(item.source)}
                      >
                        <FileType format={item.source.format} />
                        {item.source.name}
                        {documents.some(
                          (other) =>
                            other.source.name === item.source.name &&
                            other.source.version_id !== item.source.version_id,
                        ) && (
                          <small className="tab-version">
                            {item.source.version_id.slice(0, 6)}
                          </small>
                        )}
                      </button>
                      <button
                        className="close-tab"
                        aria-label={`Close ${item.source.name}${documents.some((other) => other.source.name === item.source.name && other.source.version_id !== item.source.version_id) ? ` (${item.source.version_id.slice(0, 6)})` : ''}`}
                        onClick={() => closeDocument(item.source.version_id)}
                      >
                        <Icon name="Close" />
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  className="icon-button"
                  title="Add file"
                  aria-label="Add file to project"
                  onClick={() => showDialog('add')}
                >
                  <Icon name="Plus" />
                </button>
                {document && (
                  <button
                    className="icon-button"
                    title="Toggle selection inspector"
                    aria-label="Toggle selection inspector"
                    aria-pressed={showInspector}
                    ref={inspectorToggle}
                    onClick={toggleInspector}
                  >
                    <Icon name="Info" />
                  </button>
                )}
              </div>
              {document ? (
                <section
                  id="document-panel"
                  className="document-panel"
                  role="tabpanel"
                  aria-labelledby={`file-tab-${active}`}
                >
                  <div className="document-toolbar">
                    <div className="task-tabs" aria-label="File operations">
                      {(['Data', 'Compare', 'Validate'] as Task[]).map((task) => (
                        <button
                          key={task}
                          aria-pressed={document.task === task}
                          onClick={() => updateDocument(active, (old) => ({ ...old, task }))}
                        >
                          <Icon name={task === 'Data' ? 'Explore' : task} />
                          {task}
                        </button>
                      ))}
                    </div>
                    <span className="document-format">
                      {document.source.format} · {document.source.version_id.slice(0, 8)}
                    </span>
                  </div>
                  {document.source.stale && (
                    <div className="warning source-warning">
                      This source changed or is unavailable. Cached evidence belongs to the recorded
                      version. Add the current file to make a new observation.
                      <button
                        className="secondary"
                        onClick={() => {
                          setPath(document.source.current_path || document.source.path);
                          showDialog('add');
                        }}
                      >
                        Add current observation
                      </button>
                    </div>
                  )}
                  <div className="document-content" key={`${active}-${document.task}`}>
                    {document.task === 'Data' && (
                      <DataView
                        document={document}
                        busy={busy}
                        change={changeControls}
                        run={(operation, patch) => schedule(document, operation, patch)}
                      />
                    )}
                    {document.task === 'Compare' && (
                      <CompareView
                        document={document}
                        artifacts={artifacts}
                        busy={busy}
                        change={changeControls}
                        run={(operation, patch) => schedule(document, operation, patch)}
                      />
                    )}
                    {document.task === 'Validate' && (
                      <ValidateView
                        document={document}
                        contracts={contracts}
                        busy={busy}
                        change={changeControls}
                        run={(operation, patch) => schedule(document, operation, patch)}
                        onPickContract={nativeDesktop ? () => void pickFile(true) : undefined}
                      />
                    )}
                  </div>
                </section>
              ) : (
                <FileOverview
                  artifacts={artifacts}
                  onOpen={openDocument}
                  onAdd={() => showDialog('add')}
                />
              )}
            </div>
            {showInspector && document && (
              <>
                <Divider
                  label="Resize selection inspector"
                  value={inspectorWidth}
                  change={setInspectorWidth}
                  min={190}
                  max={400}
                  reversed
                />
                <Inspector
                  document={document}
                  onHide={hideInspector}
                  onRuns={() => {
                    setRunVersion(active);
                    setFocusedRun('');
                    setView('Runs');
                  }}
                  onLineage={() => {
                    setGraphFocus(active);
                    setView('Lineage');
                  }}
                />
              </>
            )}
          </div>
        )}
        {view === 'Runs' && (
          <div className="project-view">
            <Runs
              versionId={runVersion}
              focus={focusedRun}
              onVersion={openVersion}
              onClearFilter={() => setRunVersion(undefined)}
              fileName={artifacts.find((file) => file.version_id === runVersion)?.name}
            />
          </div>
        )}
        {view === 'Lineage' && (
          <div className="project-view">
            <Lineage
              versionId={graphFocus || active || undefined}
              onVersion={openVersion}
              files={artifacts}
              onRun={(id) => {
                setFocusedRun(id);
                setRunVersion(undefined);
                setView('Runs');
              }}
            />
          </div>
        )}
        {view === 'Reports' && (
          <div className="reports-workspace">
            <aside className="report-browser">
              <div className="panel-heading">
                <strong>
                  Saved reports <span>{reports.length}</span>
                </strong>
              </div>
              <Search label="Find reports" value={reportQuery} onChange={setReportQuery} />
              <select
                aria-label="Report type"
                value={reportKind}
                onChange={(event) => setReportKind(event.target.value)}
              >
                <option value="">All report types</option>
                <option value="diff">Comparisons</option>
                <option value="validate">Validations</option>
                <option value="profile">Profiles</option>
              </select>
              <div className="report-list">
                {visibleReports.map((report) => (
                  <button
                    key={report.id}
                    className={`report-row ${selectedReport?.report_id === report.id ? 'selected' : ''}`}
                    aria-pressed={selectedReport?.report_id === report.id}
                    onClick={() => void loadReport(report.id)}
                  >
                    <span>
                      <strong>
                        {report.kind === 'diff'
                          ? 'Comparison'
                          : report.kind === 'validate'
                            ? 'Validation'
                            : 'Profile'}
                      </strong>
                      {report.outcome && <Badge value={report.outcome} />}
                    </span>
                    <small>{report.inputs?.map((input: Data) => input.name).join(' / ')}</small>
                    <time>{new Date(report.created_at).toLocaleString()}</time>
                  </button>
                ))}
                {!visibleReports.length && (
                  <p className="inline-empty">
                    No matching saved reports. Run a comparison, validation or profile to create
                    one.
                  </p>
                )}
              </div>
            </aside>
            <section className="report-detail">
              {reportLoading ? (
                <p role="status">Loading saved evidence…</p>
              ) : selectedReport ? (
                <>
                  <div className="report-toolbar">
                    <h1>
                      {selectedReport.kind === 'diff'
                        ? 'Comparison report'
                        : selectedReport.kind === 'validate'
                          ? 'Validation report'
                          : 'Profile report'}
                    </h1>
                    <div>
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={overwrite}
                          onChange={(event) => setOverwrite(event.target.checked)}
                        />
                        Overwrite export
                      </label>
                      <button
                        className="secondary"
                        disabled={exporting}
                        onClick={() => void exportReport('json')}
                      >
                        Export JSON
                      </button>
                      <button disabled={exporting} onClick={() => void exportReport('html')}>
                        Export HTML
                      </button>
                    </div>
                  </div>
                  <p className="scope-note">
                    Saved evidence for its recorded source versions. Exports include bounded source
                    examples.
                  </p>
                  {selectedReport.kind === 'validate' && (
                    <button
                      className="text-button"
                      onClick={() => {
                        setGraphFocus(selectedReport.report_id);
                        setView('Lineage');
                      }}
                    >
                      Receipt dependencies
                    </button>
                  )}
                  <ResultView result={selectedReport} />
                </>
              ) : (
                <Empty icon="Reports" title="Select a saved report">
                  <p>
                    Comparisons, validations and profiles retain their source identity, coverage and
                    findings. Previews stay in session memory.
                  </p>
                </Empty>
              )}
            </section>
          </div>
        )}
      </main>
      <footer className="statusbar">
        <button className="status-job" onClick={() => showDialog('jobs')} aria-label="View jobs">
          <span className={busy ? 'spinner' : 'status-dot'} />
          <span role="status">
            {cancelling
              ? 'Cancelling…'
              : submitting
                ? 'Submitting…'
                : job
                  ? `${operations[job.operation || job.request?.operation] || 'Operation'} · ${job.state.toLowerCase().replaceAll('_', ' ')}`
                  : 'Ready'}
            {pending.current && busy ? ' · next selection queued' : ''}
          </span>
        </button>
        {busy && (
          <button className="text-button" disabled={cancelling} onClick={() => void cancel()}>
            Cancel job <kbd>Esc</kbd>
          </button>
        )}
        <span className="status-scope">
          {document
            ? `${document.source.format} · ${document.controls.node === '/' ? document.source.name : document.controls.node}`
            : `${artifacts.length} project files`}
        </span>
        <span className="status-local">Read-only source access</span>
        <span>Marsd {VERSION}</span>
      </footer>
      {dialog === 'add' && (
        <Dialog
          title="Add a local file"
          onClose={() => {
            if (!adding) setDialog(null);
          }}
        >
          <p>
            Register a reference to an HDF5, Parquet or CSV file. The file stays in its original
            location.
          </p>
          <form onSubmit={addFile}>
            <label>
              Local file path
              <input
                value={path}
                onChange={(event) => setPath(event.target.value)}
                required
                autoFocus
                placeholder="Path inside an approved source folder"
              />
            </label>
            {nativeDesktop && (
              <button
                type="button"
                className="secondary"
                disabled={adding}
                onClick={() => void pickFile()}
              >
                Choose source file
              </button>
            )}
            <details>
              <summary>Approved source folders</summary>
              <ul>
                {project?.source_roots?.map((root: string) => (
                  <li key={root}>
                    <code>{root}</code>
                  </li>
                ))}
              </ul>
            </details>
            {dialogError && (
              <p className="error" role="alert">
                {dialogError}
              </p>
            )}
            <div className="dialog-actions">
              <button
                type="button"
                className="secondary"
                disabled={adding}
                onClick={() => setDialog(null)}
              >
                Cancel
              </button>
              <button disabled={adding}>{adding ? 'Adding…' : 'Add file'}</button>
            </div>
          </form>
        </Dialog>
      )}
      {dialog === 'project' && (
        <Dialog title="Project information" onClose={() => setDialog(null)}>
          <h3>{project?.name}</h3>
          <p className="path-value">{project?.path}</p>
          <h3>Approved source folders</h3>
          <ul>
            {project?.source_roots?.map((root: string) => (
              <li key={root}>
                <code>{root}</code>
              </li>
            ))}
          </ul>
          <h3>Reports and exports</h3>
          <code>{project?.outputs}</code>
          <p className="scope-note">
            One writer owns this project. Close the project before using writing CLI commands.
            Backups contain metadata, not source data.
          </p>
          <div className="dialog-actions">
            <button
              className="secondary"
              onClick={async () => {
                try {
                  if (nativeDesktop) await closeNativeProject();
                  else {
                    sessionEpoch.current += 1;
                    refreshSequence.current += 1;
                    pending.current = null;
                    submissionRef.current = null;
                    setCancelling(false);
                    busyRef.current = false;
                    setJob(null);
                    setSubmitting(false);
                    forgetSession();
                    setUnlocked(false);
                    setDocuments([]);
                    setActive('');
                    reportSequence.current += 1;
                    setProject(null);
                    setArtifacts([]);
                    setContracts([]);
                    setReports([]);
                    setJobs([]);
                    setSelectedReport(null);
                    setReportLoading(false);
                    setExporting(false);
                    setReportQuery('');
                    setReportKind('');
                    setOverwrite(false);
                    setRunVersion(undefined);
                    setFocusedRun('');
                    setGraphFocus('');
                    setPath('');
                    setNotice('');
                    setError('');
                    setView('Workbench');
                  }
                  setDialog(null);
                } catch (reason) {
                  setDialogError(String(reason));
                }
              }}
            >
              {nativeDesktop ? 'Close project' : 'Lock browser session'}
            </button>
          </div>
          {dialogError && (
            <p className="error" role="alert">
              {dialogError}
            </p>
          )}
        </Dialog>
      )}
      {dialog === 'help' && (
        <Dialog title="Workbench reference" onClose={() => setDialog(null)}>
          <dl className="shortcut-list">
            <dt>
              <kbd>Alt</kbd> + <kbd>1…8</kbd>
            </dt>
            <dd>Switch open documents</dd>
            <dt>
              <kbd>Ctrl / ⌘</kbd> + <kbd>Enter</kbd>
            </dt>
            <dd>Update the current data view (outside input fields)</dd>
            <dt>
              <kbd>Esc</kbd>
            </dt>
            <dd>Close a dialog or cancel the active read</dd>
            <dt>
              <kbd>↑</kbd> / <kbd>↓</kbd>
            </dt>
            <dd>Move through dataset buttons</dd>
            <dt>
              <kbd>←</kbd> / <kbd>→</kbd>
            </dt>
            <dd>Switch file tabs or resize a focused divider</dd>
          </dl>
          <h3>Read scopes and exact values</h3>
          <p>
            Opening a file reads bounded structure and a supported preview. Array slices accept
            indices or <code>start:stop:step</code>; rank-three arrays need an explicit fixed index.
            Large integers remain exact in values tables.
          </p>
          <h3>Evidence boundaries</h3>
          <p>
            Coverage is always explicit. Size/timestamp identity is weak. Source consistency is best
            effort. Parsers run in cancellable workers, without an OS sandbox guarantee. Runs review
            recorded evidence and never execute target code.
          </p>
          <h3>CLI and recovery</h3>
          <p>
            Close the project before writing CLI commands. Use <code>marsd --help</code> for backup,
            restore and source relocation. Desktop bundles the CLI as <code>marsd-backend</code> in{' '}
            <code>runtime/backend</code>. Backups do not include source data.
          </p>
        </Dialog>
      )}
      {dialog === 'jobs' && (
        <Dialog title="Project jobs" onClose={() => setDialog(null)}>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>State</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {[...(job ? [job] : []), ...jobs.filter((item) => item.id !== job?.id)].map(
                  (item) => (
                    <tr key={item.id}>
                      <td>
                        {operations[item.operation || item.request?.operation] || item.operation}
                      </td>
                      <td>
                        <Badge value={item.state} />
                      </td>
                      <td className="path-value">{item.request?.path || '—'}</td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
          {!jobs.length && !job && <p>No jobs in this project yet.</p>}
        </Dialog>
      )}
    </div>
  );
}
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <DesktopRoot>
      <App />
    </DesktopRoot>
  </React.StrictMode>,
);
