import { useState } from 'react';
import { display, type Data } from './api';
import { Badge } from './ui';

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
    return (
      <p>Switch to Values to inspect large integers exactly. This selection is not plotted.</p>
    );
  const finite = flat.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  if (!finite.length)
    return <p>No finite numeric values in this selection. Switch to Values to inspect the data.</p>;
  const low = Math.min(...finite),
    high = Math.max(...finite),
    range = high - low || 1;
  const width = 680,
    height = matrix ? 390 : 310,
    top = 20;
  const cell = matrix ? Math.min(485 / raw[0].length, 320 / raw.length) : 0;
  const areaW = matrix ? cell * raw[0].length : 590;
  const areaH = matrix ? cell * raw.length : 245;
  const left = matrix ? (width - areaW - 100) / 2 : 58;
  const color = (value: number) =>
    `hsl(${202 - ((value - low) / range) * 34} 48% ${89 - ((value - low) / range) * 63}%)`;
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
          <>
            <g className="heatmap-cells">
              {raw.map((row: unknown[], y: number) =>
                row.map((v, x) => (
                  <rect
                    key={`${x}-${y}`}
                    x={left + x * cell}
                    y={top + y * cell}
                    width={cell + 0.2}
                    height={cell + 0.2}
                    fill={typeof v === 'number' && Number.isFinite(v) ? color(v) : '#c1c9ce'}
                  >
                    <title>{`[${y}, ${x}]: ${display(v)}`}</title>
                  </rect>
                )),
              )}
            </g>
            <defs>
              <linearGradient id="array-color-scale" x1="0" y1="1" x2="0" y2="0">
                {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                  <stop key={t} offset={t} stopColor={color(low + t * (high - low))} />
                ))}
              </linearGradient>
            </defs>
            <rect
              x={left + areaW + 24}
              y={top}
              width={12}
              height={areaH}
              fill="url(#array-color-scale)"
              rx="2"
            />
            <text x={left + areaW + 43} y={top + 9}>
              {high.toPrecision(3)}
            </text>
            <text x={left + areaW + 43} y={top + areaH}>
              {low.toPrecision(3)}
            </text>
            <text x={left - 12} y={top + 10} textAnchor="end">
              Row 0
            </text>
            <text x={left - 12} y={top + areaH} textAnchor="end">
              {raw.length - 1}
            </text>
          </>
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
        <text x={left} y={top + areaH + 24}>
          {matrix ? 'Selection column 0' : 'Selection index 0'}
        </text>
        <text x={left + areaW} y={top + areaH + 24} textAnchor="end">
          {matrix ? raw[0]?.length - 1 : raw.length - 1}
        </text>
      </svg>
      <div className="plot-caption">
        <span>{result.array?.path}</span>
        <span>
          {result.array?.unit || 'Unit unspecified'} · finite range {low.toPrecision(4)} to{' '}
          {high.toPrecision(4)}
          {matrix && ' · equal-sized index cells'}
        </span>
      </div>
    </div>
  );
}

function DataTable({
  fields,
  rows,
  offset = 0,
}: {
  fields: string[];
  rows: Data[];
  offset?: number;
}) {
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
              <td className="row-number">{offset + i}</td>
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

export function ResultView({
  result,
  representation,
  onRepresentationChange,
}: {
  result: Data;
  representation?: 'plot' | 'values';
  onRepresentationChange?: (view: 'plot' | 'values') => void;
}) {
  const [localView, setLocalView] = useState<'plot' | 'values'>('plot');
  const arrayView = representation || localView;
  const setArrayView = onRepresentationChange || setLocalView;
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
      {table && (
        <DataTable
          fields={result.fields.map((f: Data) => f.name)}
          rows={result.rows}
          offset={result.offset || 0}
        />
      )}
      {result.kind === 'preview' && result.array && (
        <>
          <div className="array-view-switch">
            <div className="segmented" aria-label="Array representation">
              <button aria-pressed={arrayView === 'plot'} onClick={() => setArrayView('plot')}>
                Plot
              </button>
              <button aria-pressed={arrayView === 'values'} onClick={() => setArrayView('values')}>
                Values
              </button>
            </div>
          </div>
          {arrayView === 'plot' && <Plot result={result} />}
          {arrayView === 'values' && (
            <div>
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
            </div>
          )}
        </>
      )}
      {result.kind === 'diff' && (
        <>
          <p className="scope-note">
            Completed comparison:{' '}
            {result.inputs
              ?.map((input: Data) => input.name || input.path?.split(/[/\\]/).pop())
              .join(' / ')}
            . Findings reflect the submitted settings.
          </p>
          <div className="finding-summary">
            <p>
              <strong>{result.different_cells ?? '—'}</strong> differing cells
            </p>
            <p>
              <strong>{result.structural_changes?.length ?? '—'}</strong> structural changes shown
            </p>
            {(result.unmatched_a !== undefined || result.unmatched_b !== undefined) && (
              <p>
                <strong>{(result.unmatched_a ?? 0) + (result.unmatched_b ?? 0)}</strong> unmatched
                rows
              </p>
            )}
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
        <>
          {result.statistics && (
            <dl className="profile-statistics">
              {Object.entries(result.statistics).map(([name, value]) => (
                <div key={name}>
                  <dt>{name.replaceAll('_', ' ')}</dt>
                  <dd>{display(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          {result.fields && !Array.isArray(result.fields) && (
            <DataTable
              fields={['Column', ...Object.keys(Object.values(result.fields)[0] || {})]}
              rows={Object.entries(result.fields).map(([name, statistics]) => ({
                Column: name,
                ...(statistics as Data),
              }))}
            />
          )}
          <p className="scope-note">
            Means use a float64 approximation. Coverage describes the scanned scope; string columns
            are not implicitly converted to numbers.
          </p>
        </>
      )}
      <JsonDetails value={result} />
    </section>
  );
}
