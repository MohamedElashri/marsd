import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, authenticated, display, forgetSession, login, type Data } from './api';
import { VERSION, type Request as WorkRequest } from './schema';
import './style.css';
import { DesktopRoot, nativeDesktop, closeNativeProject, pickNativePath } from './desktop';
import { Runs, Lineage } from './evidence';

type Tab =
  'Explore' | 'Compare' | 'Validate' | 'Runs' | 'Lineage' | 'Reports' | 'Settings' | 'Help';
const tabs: Tab[] = [
  'Explore',
  'Compare',
  'Validate',
  'Runs',
  'Lineage',
  'Reports',
  'Settings',
  'Help',
];
const finished = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'INTERRUPTED']);

function Mark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path d="M4 24V8h5l7 12 7-12h5v16" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="M4 29h24" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}
function Badge({ value }: { value: string }) {
  return <span className={`badge ${value.toLowerCase()}`}>{value}</span>;
}
function JsonDetails({ value, title = 'Full evidence' }: { value: unknown; title?: string }) {
  return (
    <details className="evidence">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function Plot({ result }: { result: Data }) {
  const raw = result.values;
  if (!Array.isArray(raw)) return <p className="muted">Scalar value: {display(raw)}</p>;
  const matrix = Array.isArray(raw[0]);
  const flat: unknown[] = matrix ? raw.flat() : raw;
  if (flat.some((v) => v !== null && typeof v === 'object' && '$int' in (v as Data)))
    return <p>Large integers remain exact in the values table below; the plot is omitted.</p>;
  const finite = flat.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  if (!finite.length)
    return <p>No finite numeric values in this selection. Inspect the data table.</p>;
  const low = Math.min(...finite),
    high = Math.max(...finite),
    range = high - low || 1;
  const width = 680,
    height = 310,
    left = 58,
    top = 20,
    areaW = 590,
    areaH = 245;
  return (
    <div className="plot-wrap">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={
          matrix
            ? 'Heatmap of the selected array. Values are also available in the table below.'
            : 'Line plot of the selected array. Values are also available in the table below.'
        }
      >
        <rect x={left} y={top} width={areaW} height={areaH} fill="#edf3f7" />
        {matrix ? (
          raw.map((row: unknown[], y: number) =>
            row.map((v, x) => (
              <rect
                key={`${x}-${y}`}
                x={left + (x * areaW) / row.length}
                y={top + (y * areaH) / raw.length}
                width={areaW / row.length + 0.2}
                height={areaH / raw.length + 0.2}
                fill={
                  typeof v === 'number' && Number.isFinite(v)
                    ? `hsl(${202 - ((v - low) / range) * 34} 48% ${89 - ((v - low) / range) * 63}%)`
                    : '#c1c9ce'
                }
              >
                <title>{`[${y}, ${x}]: ${display(v)}`}</title>
              </rect>
            )),
          )
        ) : (
          <>
            {[0, 0.25, 0.5, 0.75, 1].map((t) => (
              <g key={t}>
                <line
                  x1={left}
                  x2={left + areaW}
                  y1={top + t * areaH}
                  y2={top + t * areaH}
                  stroke="#ccd8e1"
                />
                <text x={left - 10} y={top + t * areaH + 4} textAnchor="end">
                  {(high - t * range).toPrecision(3)}
                </text>
              </g>
            ))}
            <path
              d={raw
                .map((v: unknown, i: number) =>
                  typeof v === 'number' && Number.isFinite(v)
                    ? `${i && typeof raw[i - 1] === 'number' && Number.isFinite(raw[i - 1]) ? 'L' : 'M'}${left + (i * areaW) / Math.max(1, raw.length - 1)},${top + areaH - ((v - low) / range) * areaH}`
                    : '',
                )
                .join(' ')}
              stroke="#146f7b"
              strokeWidth="2.5"
              fill="none"
            />
          </>
        )}
        <line x1={left} x2={left + areaW} y1={top + areaH} y2={top + areaH} stroke="#8296a6" />
        <text x={left} y={height - 20}>
          Selection index 0
        </text>
        <text x={left + areaW} y={height - 20} textAnchor="end">
          {matrix ? raw[0]?.length - 1 : raw.length - 1}
        </text>
        {matrix && (
          <text x={12} y={top + 12}>
            Row 0
          </text>
        )}
      </svg>
      <div className="plot-caption">
        <span>{result.array?.path}</span>
        <span>
          {result.array?.unit || 'Unit unspecified'} · finite range {low.toPrecision(4)} to{' '}
          {high.toPrecision(4)}
        </span>
      </div>
    </div>
  );
}

function DataTable({ fields, rows }: { fields: string[]; rows: Data[] }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th className="row-number">Row</th>
            {fields.map((f) => (
              <th key={f}>{f}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="row-number">{i}</td>
              {fields.map((f) => (
                <td key={f} className={r[f] === null ? 'null-value' : ''}>
                  {display(r[f])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <p className="empty-inline">This selection contains no rows.</p>}
    </div>
  );
}

function ResultView({ result }: { result: Data }) {
  const coverage = result.coverage || {};
  const table = result.kind === 'preview' && result.rows;
  return (
    <section className="results" aria-live="polite">
      <div className="result-heading">
        <h2>
          {result.kind === 'diff'
            ? 'Comparison findings'
            : result.kind === 'validate'
              ? 'Validation receipt'
              : result.kind === 'profile'
                ? 'Data profile'
                : 'Selected data'}
        </h2>
        {result.outcome && <Badge value={result.outcome} />}
      </div>
      <div className={`coverage ${coverage.complete ? 'complete' : 'partial'}`}>
        <strong>
          {coverage.complete ? 'Requested scope complete' : 'Limited or incomplete coverage'}
        </strong>
        <span>
          {coverage.strategy || coverage.scope || 'See input coverage'}
          {coverage.visited !== undefined ? ` · ${coverage.visited} visited` : ''}
          {coverage.total != null ? ` of ${coverage.total}` : ''}. Source consistency: best effort.
        </span>
      </div>
      {result.inputs?.some((s: Data) => s.stale) && (
        <p className="warning">
          A source changed or became unavailable after this result was recorded. Refresh the source
          before relying on it.
        </p>
      )}
      {table && <DataTable fields={result.fields.map((f: Data) => f.name)} rows={result.rows} />}
      {result.kind === 'preview' && result.array && (
        <>
          <Plot result={result} />
          <details>
            <summary>Show selected values as a table</summary>
            {Array.isArray(result.values) ? (
              <DataTable
                fields={
                  Array.isArray(result.values[0])
                    ? result.values[0].map((_: unknown, i: number) => String(i))
                    : ['value']
                }
                rows={
                  Array.isArray(result.values[0])
                    ? result.values.map((row: unknown[]) =>
                        Object.fromEntries(row.map((v, i) => [String(i), v])),
                      )
                    : result.values.map((v: unknown) => ({ value: v }))
                }
              />
            ) : (
              <p>{display(result.values)}</p>
            )}
          </details>
        </>
      )}
      {result.kind === 'diff' && (
        <>
          <div className="finding-summary">
            <p>
              <strong>{result.different_cells ?? 0}</strong> differing cells
            </p>
            <p>
              <strong>{result.structural_changes?.length ?? 0}</strong> structural changes shown
            </p>
            <p>
              <strong>{(result.unmatched_a ?? 0) + (result.unmatched_b ?? 0)}</strong> unmatched
              rows
            </p>
          </div>
          {result.limitation && <p className="warning">{result.limitation}</p>}
          {result.inconclusive_fields?.length > 0 && (
            <p className="warning">
              Unit evidence prevents comparison of: {result.inconclusive_fields.join(', ')}.
            </p>
          )}
          {result.examples?.length > 0 && (
            <DataTable fields={Object.keys(result.examples[0])} rows={result.examples} />
          )}
          <JsonDetails
            title="Structural and unit evidence"
            value={{
              changes: result.structural_changes,
              units: result.units,
              alignment: result.alignment,
              tolerance: result.tolerance,
            }}
          />
        </>
      )}
      {result.kind === 'validate' && (
        <>
          <p className="muted">
            {result.contract.name}, revision {result.contract.revision}. All declared rules are
            mandatory.
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Outcome</th>
                  <th>Rule / field</th>
                  <th>Evidence</th>
                  <th>Violations</th>
                </tr>
              </thead>
              <tbody>
                {result.findings.map((f: Data) => (
                  <tr key={f.rule_id}>
                    <td>
                      <Badge value={f.status} />
                    </td>
                    <td>
                      <strong>{f.rule_id}</strong>
                      <small>{f.field}</small>
                    </td>
                    <td>
                      {f.reason}
                      <small>
                        {f.coverage.complete ? 'Complete rule scope' : 'Incomplete evidence'}
                      </small>
                    </td>
                    <td>{f.violations}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {result.kind === 'profile' && (
        <JsonDetails value={result.statistics || result.fields} title="Profile statistics (open)" />
      )}
      <JsonDetails value={result} />
    </section>
  );
}

function App() {
  const [unlocked, setUnlocked] = useState(authenticated);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [project, setProject] = useState<Data | null>(null);
  const [artifacts, setArtifacts] = useState<Data[]>([]);
  const [contracts, setContracts] = useState<Data[]>([]);
  const [reports, setReports] = useState<Data[]>([]);
  const [selected, setSelected] = useState<Data | null>(null);
  const selectedRef = useRef('');
  const selectedVersionRef = useRef('');
  const pendingInspection = useRef<Data | null>(null);
  const [focusedRun, setFocusedRun] = useState('');
  const [graphFocus, setGraphFocus] = useState('');
  const [tab, setTab] = useState<Tab>('Explore');
  const [metadata, setMetadata] = useState<Data | null>(null);
  const [node, setNode] = useState('/');
  const [selection, setSelection] = useState('');
  const [columns, setColumns] = useState('');
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<Data | null>(null);
  const [activeJob, setActiveJob] = useState<Data | null>(null);
  const [registerPath, setRegisterPath] = useState('');
  const [other, setOther] = useState('');
  const [keys, setKeys] = useState('');
  const [position, setPosition] = useState(false);
  const [atol, setAtol] = useState('0');
  const [rtol, setRtol] = useState('0');
  const [convertUnits, setConvertUnits] = useState(false);
  const [nanEqual, setNanEqual] = useState(false);
  const [contract, setContract] = useState('');
  const [maxRows, setMaxRows] = useState(100000);
  const [overwrite, setOverwrite] = useState(false);
  const busy = Boolean(activeJob && !finished.has(activeJob.state));
  const isArray = selected?.format === 'HDF5';
  const currentNode = metadata?.nodes?.find((n: Data) => n.path === node);

  async function refresh() {
    const [p, a, c, r, j] = await Promise.all([
      api('project'),
      api<Data[]>('artifacts'),
      api<Data[]>('contracts'),
      api<Data[]>('reports'),
      api<Data[]>('jobs'),
    ]);
    setProject(p);
    setArtifacts(a);
    setContracts(c);
    setReports(r);
    // Navigation keeps its explicit version; refresh never silently substitutes a newer observation.
    const running = j.find((item) => !finished.has(item.state));
    if (running) setActiveJob((old) => (old && old.id === running.id ? old : running));
    setContract((old) => old || c.find((x) => !x.invalid)?.path || '');
  }
  useEffect(() => {
    if (unlocked) refresh().catch((e) => setError(e.message));
  }, [unlocked]);
  useEffect(() => {
    if (!activeJob || finished.has(activeJob.state)) return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const job = await api(`jobs/${activeJob.id}`);
        if (stopped) return;
        if (finished.has(job.state)) {
          window.clearInterval(timer);
          setActiveJob(job);
          if (job.state === 'SUCCEEDED') {
            const value = job.result;
            if (
              value?.inputs?.[0]?.path === selectedRef.current &&
              value?.inputs?.[0]?.version_id === selectedVersionRef.current
            ) {
              if (value.kind === 'inspect') {
                setMetadata(value);
                const candidate =
                  value.nodes?.find((n: Data) => n.supported && n.shape?.length === 2) ||
                  value.nodes?.find((n: Data) => n.supported);
                if (candidate) setNode(candidate.path);
                setResult(null);
              } else setResult(value);
            } else
              setNotice(
                value?.report_id
                  ? 'Job completed for another artifact. Its saved report is available in Reports.'
                  : 'A preview completed for a previous selection. Select that artifact and rerun to view it.',
              );
            await refresh();
          } else if (job.state !== 'CANCELLED')
            setError(
              `${job.error?.code || job.state}: ${job.error?.message || 'The operation did not complete.'} ${job.error?.suggested_action || ''}`,
            );
          const next = pendingInspection.current;
          pendingInspection.current = null;
          if (next && next.version_id === selectedVersionRef.current)
            void run('inspect', { node: '/', selection: '', columns: [], offset: 0 }, next);
        } else setActiveJob(job);
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    }, 200);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeJob?.id, activeJob?.state]);

  async function run(
    operation: WorkRequest['operation'],
    overrides: Partial<WorkRequest> = {},
    source = selected,
  ) {
    if (!source) return;
    setError('');
    setNotice('');
    setResult(null);
    try {
      const request: WorkRequest = {
        operation,
        path: source.current_path || source.path,
        version_id: source.version_id,
        node,
        selection,
        columns: columns
          ? columns
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean)
          : [],
        offset,
        limit: 64,
        max_rows: maxRows,
        ...overrides,
      };
      const job = await api('jobs', request);
      setActiveJob({ id: job.id, state: 'QUEUED', operation });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function choose(artifact: Data, inspectSource = true) {
    pendingInspection.current = null;
    setSelected(artifact);
    selectedRef.current = artifact.current_path || artifact.path;
    selectedVersionRef.current = artifact.version_id;
    setFocusedRun('');
    setGraphFocus('');
    setMetadata(null);
    setResult(null);
    setNode('/');
    setSelection('');
    setOffset(0);
    setColumns('');
    setError('');
    if (artifact.stale)
      setError(
        'VERSION_UNAVAILABLE: This source changed or is unavailable. Register the current file deliberately before opening a new observation.',
      );
    if (inspectSource && !artifact.stale) {
      if (busy) pendingInspection.current = artifact;
      else void run('inspect', { node: '/', selection: '' }, artifact);
    }
  }
  async function openVersion(id: string, destination: 'Explore' | 'Validate' | 'Runs' | 'Lineage') {
    try {
      const version = await api(`versions/${encodeURIComponent(id)}`);
      choose(version, destination === 'Explore');
      setTab(destination);
      if (version.stale)
        setNotice(
          'This recorded version changed or is unavailable. Historical values are not retained; saved run and lineage evidence remain available.',
        );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function exportResult(format: 'json' | 'html') {
    if (!result?.report_id) return;
    try {
      const value = await api(`reports/${result.report_id}/export`, { format, overwrite });
      setNotice(`Saved ${value.path}. Exports include bounded source examples.`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!unlocked)
    return (
      <main className="unlock">
        <div className="brand">
          <Mark />
          <span>
            Marsd <b lang="ar">مرصد</b>
          </span>
        </div>
        <div className="unlock-content">
          <span className="session-symbol" aria-hidden="true">
            ⌁
          </span>
          <h1>Your data, in view.</h1>
          <p>
            A local workspace for inspecting scientific files, comparing versions and checking data
            contracts.
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setError('');
              try {
                await login(code.trim());
                setCode('');
                setUnlocked(true);
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            <label htmlFor="launch">Launch code</label>
            <div className="input-action">
              <input
                id="launch"
                type="password"
                autoComplete="off"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="Paste the code printed in your terminal"
                required
              />
              <button type="submit">Open workspace</button>
            </div>
            <small>
              The code stays out of URLs and browser history. This session connects only to your
              computer.
            </small>
          </form>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <details>
            <summary>Starting a new project</summary>
            <p>
              In your terminal, run <code>marsd fixtures generate --output ./demo --seed 42</code>,
              then <code>marsd open ./demo</code>. For your own files, use{' '}
              <code>marsd project create PATH --source-root DATA_DIRECTORY</code>.
            </p>
          </details>
        </div>
        <footer>Local by design. Source files remain read-only.</footer>
      </main>
    );

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <Mark />
          <span>
            Marsd <b lang="ar">مرصد</b>
          </span>
        </div>
        <div className="project-name">
          <span className="project-dot" />
          {project?.name || 'Opening project…'}
        </div>
        <div className="local-status">
          <span />
          Local session
        </div>
      </header>
      <div className="workspace">
        <aside className="sidebar">
          <div className="sidebar-heading">
            <h2>Artifacts</h2>
            <button
              className="icon-button"
              aria-label="Refresh artifacts"
              title="Refresh artifacts"
              onClick={() => refresh().catch((e) => setError(e.message))}
            >
              ↻
            </button>
          </div>
          <p className="sidebar-note">Registered by reference</p>
          <div className="artifact-list">
            {artifacts.map((a) => (
              <button
                key={a.id}
                className={`artifact ${selected?.id === a.id ? 'selected' : ''}`}
                onClick={() => choose(a)}
              >
                <span className="file-type">
                  {a.format === 'Parquet' ? 'PQ' : a.format === 'HDF5' ? 'H5' : 'CSV'}
                </span>
                <span>
                  <strong>{a.name}</strong>
                  <small>{a.stale ? 'Changed or unavailable' : a.format}</small>
                </span>
                {a.stale && <span className="stale-dot" title="Changed or unavailable" />}
              </button>
            ))}
          </div>
          <details className="register-form">
            <summary>Register a file</summary>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await api('artifacts', { path: registerPath });
                  setRegisterPath('');
                  await refresh();
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              {nativeDesktop && (
                <button
                  type="button"
                  className="secondary"
                  onClick={async () => {
                    try {
                      const path = await pickNativePath(false);
                      if (path) setRegisterPath(path);
                    } catch (e) {
                      setError(String(e));
                    }
                  }}
                >
                  Choose source file
                </button>
              )}
              <label>
                Local path
                <input
                  value={registerPath}
                  onChange={(e) => setRegisterPath(e.target.value)}
                  required
                  placeholder="Path inside an approved root"
                />
              </label>
              <button className="secondary" type="submit">
                Register reference
              </button>
            </form>
          </details>
          {metadata?.nodes && (
            <div className="hierarchy">
              <h3>Inside this file</h3>
              {metadata.nodes.map((n: Data) => (
                <button
                  key={n.path}
                  className={`node ${node === n.path ? 'selected' : ''}`}
                  title={n.limitation || n.path}
                  onClick={() => {
                    setNode(n.path);
                    setSelection('');
                  }}
                >
                  <span>{n.kind === 'group' ? '▾' : n.kind === 'link' ? '↗' : '▦'}</span>
                  <span>
                    {n.path === '/' ? '/' : n.path.split('/').pop()}
                    <small>
                      {n.dtype || n.kind}
                      {n.shape && ` [${n.shape.join(' × ')}]`}
                    </small>
                  </span>
                </button>
              ))}
            </div>
          )}
          <div className="sidebar-footer">
            <span className="read-only-symbol">◈</span>
            <span>
              Inspection is read-only<small>Results stay in this project</small>
            </span>
          </div>
        </aside>
        <main className="main-workspace">
          <nav className="tabs" aria-label="Workspace views">
            {tabs.map((t) => (
              <button
                key={t}
                className={tab === t ? 'active' : ''}
                onClick={() => {
                  setTab(t);
                  setError('');
                }}
              >
                {t}
              </button>
            ))}
          </nav>
          {error && (
            <div className="error banner" role="alert">
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError('')}>
                ×
              </button>
            </div>
          )}
          {notice && (
            <div className="notice banner" role="status">
              <span>{notice}</span>
              <button aria-label="Dismiss notice" onClick={() => setNotice('')}>
                ×
              </button>
            </div>
          )}
          {activeJob && (
            <div className={`job-bar ${busy ? 'running' : ''}`} role="status">
              <span className={busy ? 'spinner' : 'job-check'}>
                {busy ? '' : activeJob.state === 'SUCCEEDED' ? '✓' : '•'}
              </span>
              <span>
                {activeJob.operation || activeJob.request?.operation} ·{' '}
                {activeJob.state.toLowerCase().replaceAll('_', ' ')}
                {activeJob.progress && ` · ${activeJob.progress.elapsed_seconds}s`}
              </span>
              {busy && (
                <button
                  className="text-button"
                  onClick={() =>
                    api(`jobs/${activeJob.id}`, undefined, 'DELETE')
                      .then(setActiveJob)
                      .catch((e) => setError(e.message))
                  }
                >
                  Cancel job
                </button>
              )}
            </div>
          )}
          <div className="content">
            {['Explore', 'Compare', 'Validate', 'Runs', 'Lineage'].includes(tab) && selected && (
              <div className="artifact-heading">
                <div>
                  <p className="breadcrumb">
                    Artifacts / {selected.format}
                    {isArray && node !== '/' ? ` / ${node}` : ''}
                  </p>
                  <h1>{selected.name}</h1>
                  <p className="source-path" title={selected.path}>
                    {selected.path}
                  </p>
                </div>
                <div className="version-label">
                  <span>
                    Version{' '}
                    {(
                      result?.inputs?.[0]?.version_id ||
                      metadata?.inputs?.[0]?.version_id ||
                      selected.version_id
                    )?.slice(0, 8)}
                  </span>
                  <small>
                    {selected.identity_strength || 'stat_weak'} · best-effort consistency
                  </small>
                  {selected.stale && <Badge value="STALE" />}
                </div>
              </div>
            )}
            {['Explore', 'Compare', 'Validate'].includes(tab) && !selected && (
              <section className="empty-state">
                <div className="empty-grid" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                </div>
                <h1>Start with a source file.</h1>
                <p>
                  Select an artifact on the left to inspect its structure, compare a version or
                  validate a handoff.
                </p>
                <p className="muted">
                  Registration keeps a reference to the original. Your data stays where it is.
                </p>
              </section>
            )}
            {selected && tab === 'Explore' && (
              <>
                <div className="explore-layout">
                  <div className="data-pane">
                    <div className="section-heading">
                      <h2>{isArray ? 'Array selection' : 'Table preview'}</h2>
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() => run('inspect')}
                      >
                        Refresh structure
                      </button>
                    </div>
                    {isArray ? (
                      <div className="selection-form">
                        <label>
                          Dataset path
                          <input
                            value={node}
                            onChange={(e) => setNode(e.target.value)}
                            placeholder="/group/dataset"
                          />
                        </label>
                        <label>
                          Selection <span className="muted">indices or start:stop:step</span>
                          <input
                            value={selection}
                            onChange={(e) => setSelection(e.target.value)}
                            placeholder="Bounded preview, or e.g. 0:32,0:32"
                          />
                        </label>
                      </div>
                    ) : (
                      <div className="selection-form">
                        <label>
                          Columns <span className="muted">comma separated; empty means all</span>
                          <input
                            value={columns}
                            onChange={(e) => setColumns(e.target.value)}
                            placeholder="All supported columns"
                          />
                        </label>
                        <label>
                          Start row
                          <input
                            type="number"
                            min="0"
                            max="100000"
                            value={offset}
                            onChange={(e) => setOffset(Number(e.target.value))}
                          />
                        </label>
                      </div>
                    )}
                    <div className="action-row">
                      <button
                        disabled={busy || (isArray && node === '/')}
                        onClick={() => run('preview')}
                      >
                        Preview selection
                      </button>
                      <button
                        className="secondary"
                        disabled={busy || (isArray && node === '/')}
                        onClick={() => run('profile')}
                      >
                        Profile within budget
                      </button>
                      <span className="muted">64 table rows / ≤10,000 array cells</span>
                    </div>
                    {metadata && !metadata.coverage?.complete && (
                      <p className="warning">
                        Metadata inspection is incomplete or reached its limit. Use a narrower path
                        or the marsd nodes command to page through additional fields or groups.
                      </p>
                    )}
                    {metadata && !isArray && (
                      <div className="schema-strip">
                        {metadata.fields?.map((f: Data) => (
                          <span key={f.name}>
                            <strong>{f.name}</strong>
                            <small>
                              {f.dtype}
                              {f.unit ? ` · ${f.unit}` : ''}
                            </small>
                          </span>
                        ))}
                      </div>
                    )}
                    {result && ['preview', 'profile'].includes(result.kind) ? (
                      <ResultView result={result} />
                    ) : (
                      <div className="preview-empty">
                        <span>▦</span>
                        <h3>
                          {metadata
                            ? 'Structure loaded. Choose what to read.'
                            : busy
                              ? 'Reading bounded metadata…'
                              : 'Load the file structure to begin.'}
                        </h3>
                        <p>
                          Preview reads only the requested scope. Profiles show whether the source
                          was fully scanned.
                        </p>
                      </div>
                    )}
                  </div>
                  <aside className="metadata-panel">
                    <h2>Selection metadata</h2>
                    <dl>
                      <dt>Format</dt>
                      <dd>{selected.format}</dd>
                      <dt>Native dtype</dt>
                      <dd>
                        {currentNode?.dtype || (metadata?.fields ? 'See column schema' : '—')}
                      </dd>
                      <dt>Shape</dt>
                      <dd>{currentNode?.shape?.join(' × ') || '—'}</dd>
                      <dt>Unit</dt>
                      <dd>
                        {display(
                          currentNode?.unit || currentNode?.attributes?.unit || 'Unspecified',
                        )}
                      </dd>
                      <dt>Capabilities</dt>
                      <dd>{metadata?.capabilities?.join(', ') || 'Not inspected'}</dd>
                    </dl>
                    {currentNode?.limitation && <p className="warning">{currentNode.limitation}</p>}
                    {currentNode?.attributes && (
                      <JsonDetails title="Attributes" value={currentNode.attributes} />
                    )}
                    <div className="metadata-note">
                      <strong>Know the limits</strong>
                      <p>
                        A preview is a selection, not a full-dataset check. Source changes
                        invalidate confidence in earlier results.
                      </p>
                    </div>
                  </aside>
                </div>
              </>
            )}
            {selected && tab === 'Compare' && (
              <section>
                <div className="section-heading">
                  <h2>Compare with a second source</h2>
                  <span className="muted">B is the tolerance reference</span>
                </div>
                <div className="form-grid">
                  <label>
                    Reference B
                    <select value={other} onChange={(e) => setOther(e.target.value)}>
                      <option value="">Choose a registered artifact</option>
                      {artifacts
                        .filter((a) => a.path !== selected.path)
                        .map((a) => (
                          <option value={a.path} key={a.id}>
                            {a.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  {isArray ? (
                    <>
                      <label>
                        Dataset path
                        <input value={node} onChange={(e) => setNode(e.target.value)} />
                      </label>
                      <label>
                        Selection
                        <input
                          value={selection}
                          onChange={(e) => setSelection(e.target.value)}
                          placeholder="Empty = full selected dataset within budget"
                        />
                      </label>
                    </>
                  ) : (
                    <>
                      <label>
                        Unique alignment keys
                        <input
                          value={keys}
                          disabled={position}
                          onChange={(e) => setKeys(e.target.value)}
                          placeholder="e.g. device_id,wavelength_nm"
                        />
                      </label>
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={position}
                          onChange={(e) => setPosition(e.target.checked)}
                        />
                        Compare by row position explicitly
                      </label>
                    </>
                  )}
                  {!isArray && (
                    <label>
                      Columns (optional, comma separated)
                      <input
                        value={columns}
                        onChange={(e) => setColumns(e.target.value)}
                        placeholder="All supported columns"
                      />
                    </label>
                  )}
                  <label>
                    Absolute tolerance
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={atol}
                      onChange={(e) => setAtol(e.target.value)}
                    />
                  </label>
                  <label>
                    Relative tolerance
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={rtol}
                      onChange={(e) => setRtol(e.target.value)}
                    />
                  </label>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={convertUnits}
                      onChange={(e) => setConvertUnits(e.target.checked)}
                    />
                    Allow explicit compatible-unit conversion
                  </label>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={nanEqual}
                      onChange={(e) => setNanEqual(e.target.checked)}
                    />
                    Treat paired NaNs as equal (reported separately)
                  </label>
                </div>
                <p className="muted">
                  Arrays align by matching path and shape. Tables require unique keys or an explicit
                  positional choice. No inferred alignment.
                </p>
                <button
                  disabled={busy || !other || (!isArray && !keys && !position)}
                  onClick={() =>
                    run('diff', {
                      other,
                      keys: position || !keys ? [] : keys.split(',').map((x) => x.trim()),
                      position,
                      atol: Number(atol),
                      rtol: Number(rtol),
                      convert_units: convertUnits,
                      nan_equal: nanEqual,
                    })
                  }
                >
                  Compare A with B
                </button>
                {result?.kind === 'diff' && <ResultView result={result} />}
              </section>
            )}
            {selected && tab === 'Validate' && (
              <section>
                <div className="section-heading">
                  <h2>Check a data contract</h2>
                  <span className="muted">Native schema v1</span>
                </div>
                <div className="form-grid">
                  <label>
                    Contract
                    <select
                      value={contracts.some((c) => c.path === contract) ? contract : ''}
                      onChange={(e) => setContract(e.target.value)}
                    >
                      <option value="">Choose a project contract</option>
                      {contracts.map((c) => (
                        <option key={c.path} value={c.path} disabled={c.invalid}>
                          {c.name}
                          {c.invalid ? ' (invalid)' : ` · revision ${c.revision}`}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Or approved local contract path
                    <input
                      value={contract}
                      onChange={(e) => setContract(e.target.value)}
                      placeholder="Path inside approved roots"
                    />
                  </label>
                  <label>
                    Scan row/cell budget
                    <input
                      type="number"
                      min="1"
                      max="1000000"
                      value={maxRows}
                      onChange={(e) => setMaxRows(Number(e.target.value))}
                    />
                  </label>
                </div>
                {contracts.find((c) => c.path === contract)?.rules && (
                  <div className="rule-summary">
                    {contracts
                      .find((c) => c.path === contract)
                      ?.rules.map((r: Data) => (
                        <span key={r.id}>
                          {r.kind}
                          <small>{r.field}</small>
                        </span>
                      ))}
                  </div>
                )}
                <p className="muted">
                  A successful sample cannot establish a whole-file PASS. Missing reference evidence
                  is inconclusive; observed missing IDs fail membership.
                </p>
                <button disabled={busy || !contract} onClick={() => run('validate', { contract })}>
                  Validate declared rules
                </button>
                {result?.kind === 'validate' && <ResultView result={result} />}
              </section>
            )}
            {tab === 'Runs' && (
              <Runs versionId={selected?.version_id} focus={focusedRun} onVersion={openVersion} />
            )}
            {tab === 'Lineage' && (
              <Lineage
                versionId={graphFocus || selected?.version_id}
                onVersion={openVersion}
                onRun={(id) => {
                  setFocusedRun(id);
                  setTab('Runs');
                }}
              />
            )}
            {tab === 'Reports' && (
              <section>
                <h1>Investigation reports</h1>
                <p className="muted">
                  Profiles, comparisons and validation receipts stay in this project. Previews
                  remain in bounded session memory.
                </p>
                <div className="report-list">
                  {reports.length ? (
                    reports.map((r) => (
                      <button
                        key={r.id}
                        className="report-row"
                        onClick={async () => {
                          try {
                            const value = await api(`reports/${r.id}`);
                            setResult({ ...value, report_id: r.id });
                          } catch (e) {
                            setError((e as Error).message);
                          }
                        }}
                      >
                        <span>
                          <strong>{r.kind}</strong>
                          <small>{r.inputs?.map((i: Data) => i.name).join(' / ')}</small>
                        </span>
                        <span>{r.outcome && <Badge value={r.outcome} />}</span>
                        <time>{new Date(r.created_at).toLocaleString()}</time>
                      </button>
                    ))
                  ) : (
                    <p className="empty-inline">
                      Run a comparison, validation or profile to create a report.
                    </p>
                  )}
                </div>
                {result?.report_id && (
                  <>
                    <div className="export-bar">
                      <span>Exports include bounded source examples.</span>
                      {result.kind === 'validate' && (
                        <button
                          className="secondary"
                          onClick={() => {
                            setGraphFocus(result.report_id);
                            setTab('Lineage');
                          }}
                        >
                          Receipt lineage
                        </button>
                      )}
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={overwrite}
                          onChange={(e) => setOverwrite(e.target.checked)}
                        />
                        Allow overwrite
                      </label>
                      <button className="secondary" onClick={() => exportResult('json')}>
                        Export JSON
                      </button>
                      <button onClick={() => exportResult('html')}>Export HTML</button>
                    </div>
                    <ResultView result={result} />
                  </>
                )}
              </section>
            )}
            {tab === 'Settings' && (
              <section className="prose">
                <h1>Project settings</h1>
                <h2>Approved source roots</h2>
                <ul>
                  {project?.source_roots?.map((r: string) => (
                    <li key={r}>
                      <code>{r}</code>
                    </li>
                  ))}
                </ul>
                <h2>Workspace outputs</h2>
                <p>
                  <code>{project?.outputs}</code>
                </p>
                <h2>Local trust boundary</h2>
                <p>
                  Use trusted scientific files. Native parsers run in cancellable worker processes;
                  this is not an operating-system sandbox. Soft/external HDF5 links, virtual
                  datasets and external filters are not read.
                </p>
                <p>
                  One process owns the project. Stop this UI before running project-writing CLI
                  commands.
                </p>
                <button
                  className="secondary"
                  onClick={async () => {
                    if (nativeDesktop) {
                      try {
                        await closeNativeProject();
                      } catch (error) {
                        setError(String(error));
                      }
                      return;
                    }
                    forgetSession();
                    setUnlocked(false);
                    setError('Restart marsd open to obtain a fresh launch code.');
                  }}
                >
                  {nativeDesktop ? 'Close project' : 'Lock this browser session'}
                </button>
              </section>
            )}
            {tab === 'Help' && (
              <section className="prose">
                <h1>Investigate with evidence.</h1>
                <p>
                  Marsd connects exploration, comparison and contract validation around an explicit
                  artifact version.
                </p>
                <h2>Explore</h2>
                <p>
                  Select a file and inspect its structure. For HDF5, select a numeric dataset and
                  enter slices such as <code>0:32,0:32</code>. Rank-three arrays require an index
                  such as <code>0,0:32,0:32</code>. Table previews return up to 64 rows; CSV
                  pagination rescans the prefix and does not imply random access.
                </p>
                <h2>Compare</h2>
                <p>
                  Choose a reference B and explicit alignment. Tolerances use{' '}
                  <code>abs(A−B) ≤ atol + rtol × abs(B)</code>. Duplicate/null table keys are
                  rejected. Conversion is deliberate; unknown units remain inconclusive.
                </p>
                <h2>Validate</h2>
                <p>
                  Contracts support required fields, dtype, shape, nullability, range, unit
                  compatibility, uniqueness and local reference membership. A dimensionally
                  compatible unit does not prove a correct scientific quantity. Typed CSV needs
                  explicit schema/dtype rules; untyped values remain strings.
                </p>
                <h2>Interpret outcomes</h2>
                <p>
                  PASS/EQUAL requires complete requested evidence. Known violations can FAIL even
                  with incomplete counts. Missing evidence is INCONCLUSIVE. A processing failure is
                  ERROR. Identity from size and timestamps is weak; stat checks cannot guarantee
                  snapshot consistency.
                </p>
                <h2>CLI and recovery</h2>
                {nativeDesktop && (
                  <p>
                    The desktop bundles its CLI as <code>marsd-backend</code> (
                    <code>marsd-backend.exe</code> on Windows) in the application resources under{' '}
                    <code>runtime/backend</code>. Replace both <code>marsd</code>
                    and <code>python</code> in the examples below with that executable's full path.
                    It uses bundled modules; use the Python distribution with your own environment
                    for other script dependencies.
                  </p>
                )}
                <p>
                  Use <code>marsd --help</code> for commands. Stop this server before invoking
                  writing commands.{' '}
                  {nativeDesktop && 'In the desktop, close the project in Settings first. '}
                  <code>marsd project backup --project PATH</code> writes an integrity-checked
                  metadata backup. Source data is never included in a database backup. Startup marks
                  abandoned jobs interrupted.
                </p>
                <h2>Relocation and restore</h2>
                <p>
                  After moving a project or its data, use{' '}
                  <code>marsd project remap-root ORIGINAL_ROOT NEW_ROOT --project PATH</code>. The
                  default previews metadata changes. Add <code>--apply</code> to relink references
                  after checking the preview; source files are never moved or copied.
                </p>
                <p>
                  <code>marsd project restore SNAPSHOT --project PATH</code> validates a metadata
                  snapshot. Add <code>--apply</code> to restore, preserving a pre-restore snapshot
                  first. Legacy snapshots need <code>--allow-legacy</code> because their project
                  identity cannot be verified. Use <code>project cleanup</code> only for abandoned
                  internal temporary files. Removing the application or Python package leaves user
                  projects and source data in place.
                </p>
                <h2>Runs and lineage</h2>
                <p>
                  Stop the UI before using{' '}
                  <code>
                    marsd run --project PATH --input SOURCE --output OUTPUT -- python SCRIPT.py
                  </code>
                  . Only explicitly selected trusted Python scripts run. Arguments and process text
                  are omitted by default; opt-in capture is bounded and redaction is best effort.
                  Instrument functions and file helpers with <code>marsd.sdk</code>; this is not
                  automatic I/O discovery or an OS sandbox.
                </p>
                <p>
                  The Runs view compares recorded evidence and checks dry-run readiness without
                  executing code. Changed evidence does not establish a cause. Navigate by artifact
                  version ID; changed or missing historical sources cannot be silently opened as
                  current data. Import explicitly labeled JSON dependencies with{' '}
                  <code>marsd lineage-import FILE --project PATH</code>. Graph limits and missing
                  links remain visible.
                </p>
                <h2>Current scope</h2>
                <p>
                  Marsd supports browser and desktop workflows, trusted Python run recording and
                  bounded lineage. Desktop builds target macOS ARM64, Windows x86_64 and Linux
                  x86_64. Desktop distribution is experimental pending signing and baseline
                  user-machine qualification.
                </p>
              </section>
            )}
          </div>
        </main>
      </div>
      <footer className="statusbar">
        <span>{artifacts.length} registered artifacts</span>
        <span>Read-only inspection · explicit run evidence</span>
        <span>Marsd {VERSION}</span>
      </footer>
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
