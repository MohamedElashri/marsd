import { useEffect, useRef, useState } from 'react';
import { api, type Data } from './api';
import { Badge, Icon } from './ui';

type Destination = 'Explore' | 'Validate' | 'Runs' | 'Lineage';
type Navigate = (id: string, destination: Destination) => void;

function Evidence({ value }: { value: unknown }) {
  return (
    <details className="evidence">
      <summary>Recorded evidence</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function Runs({
  versionId,
  focus,
  onVersion,
  onClearFilter,
  fileName,
}: {
  versionId?: string;
  focus?: string;
  onVersion: Navigate;
  onClearFilter: () => void;
  fileName?: string;
}) {
  const [runs, setRuns] = useState<Data[]>([]);
  const [selected, setSelected] = useState<Data | null>(null);
  const [ready, setReady] = useState<Data | null>(null);
  const [comparison, setComparison] = useState<Data | null>(null);
  const [other, setOther] = useState('');
  const [error, setError] = useState('');
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [assessing, setAssessing] = useState(false);
  const [comparing, setComparing] = useState(false);

  const requestSequence = useRef(0);
  async function open(id: string) {
    const sequence = ++requestSequence.current;
    setSelected(null);
    setReady(null);
    setComparison(null);
    setOther('');
    setAssessing(false);
    setComparing(false);
    setError('');
    try {
      const detail = await api(`runs/${encodeURIComponent(id)}`);
      if (sequence === requestSequence.current) setSelected(detail);
    } catch (e) {
      if (sequence === requestSequence.current) setError((e as Error).message);
    }
  }
  useEffect(() => {
    setOffset(0);
  }, [versionId]);
  useEffect(() => {
    let active = true;
    const sequence = ++requestSequence.current;
    setLoading(true);
    setError('');
    setSelected(null);
    setReady(null);
    setComparison(null);
    setOther('');
    setAssessing(false);
    setComparing(false);
    const query = new URLSearchParams({ offset: String(offset) });
    if (versionId) query.set('version_id', versionId);
    api<Data[]>(`runs?${query}`)
      .then(async (values) => {
        if (!active) return;
        setRuns(values);
        const first = focus || values[0]?.id;
        if (first) {
          const detail = await api(`runs/${encodeURIComponent(first)}`);
          if (active && sequence === requestSequence.current) setSelected(detail);
        }
      })
      .catch((e) => {
        if (active && sequence === requestSequence.current) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      requestSequence.current += 1;
    };
  }, [versionId, offset, focus]);

  async function assess() {
    if (!selected || assessing) return;
    const sequence = requestSequence.current;
    setAssessing(true);
    setReady(null);
    setError('');
    try {
      const result = await api(`runs/${encodeURIComponent(selected.id)}/readiness`);
      if (sequence === requestSequence.current) setReady(result);
    } catch (e) {
      if (sequence === requestSequence.current) setError((e as Error).message);
    } finally {
      if (sequence === requestSequence.current) setAssessing(false);
    }
  }
  async function compare() {
    if (!selected || !other || other === selected.id || comparing) return;
    const sequence = requestSequence.current;
    setComparing(true);
    setComparison(null);
    setError('');
    try {
      const query = new URLSearchParams({ a: selected.id, b: other });
      const result = await api(`runs/compare?${query}`);
      if (sequence === requestSequence.current) setComparison(result);
    } catch (e) {
      if (sequence === requestSequence.current) setError((e as Error).message);
    } finally {
      if (sequence === requestSequence.current) setComparing(false);
    }
  }
  return (
    <section>
      <div className="section-heading">
        <h1>Recorded runs</h1>
        <span className="muted">Execution evidence, with limits</span>
      </div>
      <p className="muted">
        Record a trusted Python script with <code>marsd run</code> or the SDK. This view reviews
        saved evidence and never executes a pipeline.
      </p>
      {versionId && (
        <div className="scope-filter">
          <Icon name="Files" />
          <span>
            Related to <strong>{fileName || versionId.slice(0, 8)}</strong>
          </span>
          <button className="text-button" onClick={onClearFilter}>
            All project runs
          </button>
        </div>
      )}
      {error && (
        <p className="error banner" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">Loading recorded runs…</p>}
      {!loading && !runs.length && (
        <p className="empty-inline">
          No recorded runs in this scope. Stop the UI before using the writing CLI, then reopen the
          project.
        </p>
      )}
      <div className="run-layout">
        <div className="run-history" aria-label="Recorded run history">
          {runs.map((run) => (
            <button
              className={`run-row ${selected?.id === run.id ? 'selected' : ''}`}
              key={run.id}
              onClick={() => open(run.id)}
            >
              <strong>{run.script.split(/[/\\]/).pop()}</strong>
              <span className={`badge ${run.state.toLowerCase()}`}>{run.state}</span>
              <small>{new Date(run.started_at).toLocaleString()}</small>
              <small className="run-id">{run.id.slice(0, 8)}</small>
            </button>
          ))}
          <div className="run-pagination">
            <button
              className="secondary"
              disabled={!offset}
              onClick={() => setOffset(Math.max(0, offset - 100))}
            >
              Previous runs
            </button>
            <button
              className="secondary"
              disabled={runs.length < 100}
              onClick={() => setOffset(offset + 100)}
            >
              Next runs
            </button>
          </div>
        </div>
        {selected && (
          <article className="run-detail">
            <div className="section-heading">
              <h2>{selected.code.path.split(/[/\\]/).pop()}</h2>
              <Badge value={selected.state} />
            </div>
            <details className="runtime-details">
              <summary>Code, runtime and identity</summary>
              <dl className="run-facts">
                <dt>Selected code</dt>
                <dd>{selected.code.path}</dd>
                <dt>Script SHA-256</dt>
                <dd>
                  <code>{selected.code.sha256}</code>
                </dd>
                <dt>Interpreter</dt>
                <dd>Python {selected.environment.python}</dd>
                <dt>Process exit</dt>
                <dd>{selected.exit_code ?? 'No terminal exit evidence'}</dd>
                <dt>Text capture</dt>
                <dd>{selected.text_capture}</dd>
                <dt>Run ID</dt>
                <dd>
                  <code>{selected.id}</code>
                </dd>
              </dl>
            </details>
            {selected.reason && <p className="warning">{selected.reason}</p>}
            <h3>Inputs and outputs</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Role and evidence</th>
                    <th>Version</th>
                    <th>Condition</th>
                    <th>Open</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.references.map((ref: Data, i: number) => (
                    <tr key={i}>
                      <td>
                        {ref.role}
                        <small>
                          {ref.evidence === 'observed'
                            ? 'SDK reported operation'
                            : 'Declared by caller'}
                        </small>
                      </td>
                      <td>
                        {ref.version?.name || ref.path}
                        <small>{ref.version?.version_id || 'No artifact version evidence'}</small>
                      </td>
                      <td>
                        {ref.partial || (selected.state === 'INTERRUPTED' && ref.role === 'output')
                          ? 'Possibly partial'
                          : 'Recorded'}
                        {ref.version?.stale && (
                          <small className="warning">Changed or unavailable</small>
                        )}
                        {ref.status && <small>{ref.status}</small>}
                      </td>
                      <td>
                        {ref.version && (
                          <div className="evidence-actions">
                            <button
                              className="secondary"
                              onClick={() => onVersion(ref.version.version_id, 'Explore')}
                            >
                              Explore version
                            </button>
                            <button
                              className="secondary"
                              onClick={() => onVersion(ref.version.version_id, 'Validate')}
                            >
                              Validate version
                            </button>
                            <button
                              className="secondary"
                              onClick={() => onVersion(ref.version.version_id, 'Lineage')}
                            >
                              Version lineage
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="run-tools">
              <button onClick={assess} disabled={assessing} aria-busy={assessing}>
                Assess readiness (dry-run)
              </button>
              <label>
                Reference run
                <select
                  value={other}
                  disabled={comparing}
                  onChange={(e) => {
                    setOther(e.target.value);
                    setComparison(null);
                  }}
                >
                  <option value="">Choose another run</option>
                  {runs
                    .filter((run) => run.id !== selected.id)
                    .map((run) => (
                      <option value={run.id} key={run.id}>
                        {run.state} · {run.id.slice(0, 8)}
                      </option>
                    ))}
                </select>
              </label>
              <button
                className="secondary"
                disabled={!other || other === selected.id || comparing}
                aria-busy={comparing}
                onClick={compare}
              >
                Compare recorded evidence
              </button>
            </div>
            {assessing && <p role="status">Assessing recorded readiness…</p>}
            {comparing && <p role="status">Comparing recorded evidence…</p>}
            {ready && (
              <div className="readiness">
                <h3>{ready.status}</h3>
                <p>No target code was executed.</p>
                {[...ready.blockers, ...ready.unknowns].map((reason: string) => (
                  <p key={reason}>{reason.replaceAll('_', ' ')}</p>
                ))}
                <ul>
                  {ready.limitations.map((limit: string) => (
                    <li key={limit}>{limit}</li>
                  ))}
                </ul>
              </div>
            )}
            {comparison && (
              <div>
                <h3>Changed evidence</h3>
                <p>{comparison.limitation}</p>
                <ul>
                  {comparison.changes.map((change: Data) => (
                    <li key={change.field}>{change.field}</li>
                  ))}
                </ul>
                <Evidence value={comparison} />
              </div>
            )}
            <p className="muted">{selected.limitation}</p>
            <Evidence value={selected} />
          </article>
        )}
      </div>
    </section>
  );
}

function DependencyView({ graph, expand }: { graph: Data; expand: (id: string) => void }) {
  const levels = Math.max(0, ...graph.nodes.map((node: Data) => node.depth));
  const columns = new Map<number, Data[]>();
  for (const node of graph.nodes) {
    const level = graph.direction === 'upstream' ? levels - node.depth : node.depth;
    columns.set(level, [...(columns.get(level) || []), node]);
  }
  const height = Math.max(
    120,
    ...Array.from(columns.values()).map((nodes) => nodes.length * 75 + 20),
  );
  const width = (levels + 1) * 235 + 20;
  const positions = new Map<string, { x: number; y: number }>();
  for (const [column, nodes] of columns)
    nodes.forEach((node, i) => positions.set(node.id, { x: 15 + column * 235, y: 15 + i * 75 }));
  return (
    <div className="dependency-viewport">
      <svg
        width={width}
        height={height}
        role="img"
        aria-label="Recorded dependencies. Arrows point from prerequisites to potential dependents; full evidence is in the table below."
      >
        <defs>
          <marker
            id="dependency-arrow"
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
          >
            <path d="M0 0 L8 4 L0 8" fill="#587084" />
          </marker>
        </defs>
        {graph.edges.map((edge: Data) => {
          const a = positions.get(edge.source)!,
            b = positions.get(edge.target)!;
          return (
            <path
              key={edge.id}
              d={`M${a.x + 190},${a.y + 25} C${a.x + 215},${a.y + 25} ${b.x - 25},${b.y + 25} ${b.x},${b.y + 25}`}
              fill="none"
              stroke="#587084"
              strokeDasharray={edge.evidence === 'inferred' ? '4 4' : undefined}
              markerEnd="url(#dependency-arrow)"
            >
              <title>
                {edge.relation}: {edge.evidence}
                {edge.partial_output && <small>Possibly partial output</small>}
              </title>
            </path>
          );
        })}
        {graph.nodes.map((node: Data) => {
          const position = positions.get(node.id)!;
          return (
            <g
              key={node.id}
              transform={`translate(${position.x} ${position.y})`}
              className="dependency-node"
              role="button"
              tabIndex={0}
              aria-label={`Expand ${node.label}`}
              onClick={() => expand(node.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  expand(node.id);
                }
              }}
            >
              <title>
                {node.label} · {node.id}
              </title>
              <rect width="190" height="52" rx="4" />
              <text x="10" y="21">
                {node.label.length > 24 ? node.label.slice(0, 22) + '…' : node.label}
              </text>
              <text x="10" y="40" className="dependency-kind">
                {node.kind.replaceAll('_', ' ')}
                {node.metadata?.state ? ` · ${node.metadata.state}` : ''}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function Lineage({
  versionId,
  onVersion,
  onRun,
  files,
}: {
  versionId?: string;
  onVersion: Navigate;
  onRun: (id: string) => void;
  files: Data[];
}) {
  const [seed, setSeed] = useState(versionId || files[0]?.version_id || '');
  const [direction, setDirection] = useState('upstream');
  const [depth, setDepth] = useState(1);
  const [inferred, setInferred] = useState(false);
  const [graph, setGraph] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const querySequence = useRef(0);
  function invalidate() {
    querySequence.current += 1;
    setLoading(false);
    setGraph(null);
    setError('');
  }
  useEffect(() => {
    if (versionId) void load(versionId);
    return () => {
      querySequence.current += 1;
    };
  }, [versionId]);
  async function load(id = seed) {
    const sequence = ++querySequence.current;
    setGraph(null);
    setError('');
    setLoading(true);
    setSeed(id);
    try {
      const result = await api(
        `lineage/${encodeURIComponent(id)}?direction=${direction}&depth=${depth}&include_inferred=${inferred}`,
      );
      if (sequence === querySequence.current) setGraph(result);
    } catch (e) {
      if (sequence === querySequence.current) setError((e as Error).message);
    } finally {
      if (sequence === querySequence.current) setLoading(false);
    }
  }
  return (
    <section>
      <div className="section-heading">
        <h1>Documented dependencies</h1>
        <span className="muted">Prerequisite → potential dependent</span>
      </div>
      <p className="muted">
        Start with a version, run, validation receipt or declared scientific entity. Missing links
        remain unknown. Downstream impact does not establish scientific invalidity.
      </p>
      <form
        className="lineage-controls"
        onSubmit={(e) => {
          e.preventDefault();
          void load();
        }}
      >
        <label>
          Start with a project file
          <select
            value={files.some((file) => file.version_id === seed) ? seed : ''}
            onChange={(event) => {
              invalidate();
              setSeed(event.target.value);
            }}
          >
            <option value="">Custom entity or run</option>
            {files.map((file) => (
              <option key={file.version_id} value={file.version_id}>
                {file.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Entity or artifact version ID
          <input
            value={seed}
            onChange={(e) => {
              invalidate();
              setSeed(e.target.value);
            }}
            required
          />
        </label>
        <label>
          Direction
          <select
            value={direction}
            onChange={(e) => {
              invalidate();
              setDirection(e.target.value);
            }}
          >
            <option value="upstream">Upstream prerequisites</option>
            <option value="downstream">Downstream impact</option>
          </select>
        </label>
        <label>
          Depth
          <input
            type="number"
            min="0"
            max="20"
            value={depth}
            onChange={(e) => {
              invalidate();
              setDepth(Number(e.target.value));
            }}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={inferred}
            onChange={(e) => {
              invalidate();
              setInferred(e.target.checked);
            }}
          />
          Include labeled inferences
        </label>
        <button disabled={!seed || loading}>Load dependencies</button>
      </form>
      {error && (
        <p className="error banner" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">Loading bounded dependencies…</p>}
      {!loading && !graph && !error && (
        <p className="empty-inline">Choose a scope, then load its documented dependencies.</p>
      )}
      {graph && (
        <>
          <p>
            {graph.nodes.length} visible entities · {graph.edges.length} recorded links · at most
            200 entities / 500 links per view
          </p>
          {!graph.coverage.complete && (
            <p className="warning">
              The graph reached a depth or size limit. Expand a visible entity or choose a deeper
              explicit query; this view is not full impact.
            </p>
          )}
          {!graph.edges.length && (
            <p className="empty-inline">
              No included links in this scope. This does not prove independence.
            </p>
          )}
          <DependencyView graph={graph} expand={(id) => void load(id)} />
          <div className="graph-entity-list">
            {graph.nodes.map((node: Data) => (
              <div key={node.id}>
                <strong>{node.label}</strong>
                <small>
                  {node.kind.replaceAll('_', ' ')} · {node.id}
                </small>
                <div className="evidence-actions">
                  <button className="secondary" onClick={() => load(node.id)}>
                    Expand entity
                  </button>
                  {node.kind === 'artifact_version' && (
                    <>
                      <button className="secondary" onClick={() => onVersion(node.id, 'Explore')}>
                        Explore version
                      </button>
                      <button className="secondary" onClick={() => onVersion(node.id, 'Validate')}>
                        Validate version
                      </button>
                      <button className="secondary" onClick={() => onVersion(node.id, 'Runs')}>
                        Related runs
                      </button>
                    </>
                  )}
                  {node.kind === 'run' && (
                    <button className="secondary" onClick={() => onRun(node.id)}>
                      Review run
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="table-scroll">
            <table>
              <caption>Link evidence and direction</caption>
              <thead>
                <tr>
                  <th>Prerequisite ID</th>
                  <th>Relation</th>
                  <th>Dependent ID</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {graph.edges.map((edge: Data) => (
                  <tr key={edge.id}>
                    <td>{edge.source}</td>
                    <td>{edge.relation}</td>
                    <td>{edge.target}</td>
                    <td>
                      {edge.evidence}
                      {edge.partial_output && <small>Possibly partial output</small>}
                      <small>{edge.reference}</small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Evidence value={graph} />
        </>
      )}
    </section>
  );
}
