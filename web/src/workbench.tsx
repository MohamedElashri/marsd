import { useEffect, useState } from 'react';
import { display, type Data } from './api';
import { Badge, Empty, FileType, Icon, Search } from './ui';
import { ResultView } from './results';
import type { Request } from './schema';

export type Controls = {
  dataMode: 'preview' | 'profile';
  arrayView: 'plot' | 'values';
  readOptions: boolean;
  node: string;
  selection: string;
  columns: string;
  offset: number;
  other: string;
  keys: string;
  position: boolean;
  atol: string;
  rtol: string;
  convertUnits: boolean;
  nanEqual: boolean;
  contract: string;
  maxRows: number;
};
export type Document = {
  source: Data;
  metadata: Data | null;
  controls: Controls;
  results: Record<string, Data>;
  appliedControls: Record<string, Controls>;
  revision: number;
};
export type Operation = Request['operation'];
export const initialControls = (contract: string): Controls => ({
  dataMode: 'preview',
  arrayView: 'plot',
  readOptions: false,
  node: '/',
  selection: '',
  columns: '',
  offset: 0,
  other: '',
  keys: '',
  position: false,
  atol: '0',
  rtol: '0',
  convertUnits: false,
  nanEqual: false,
  contract,
  maxRows: 100000,
});
export function Navigator({
  artifacts,
  document,
  onOpen,
  onNode,
  onAdd,
  onRefresh,
  onHide,
}: {
  artifacts: Data[];
  document?: Document;
  onOpen: (source: Data) => void;
  onNode: (path: string) => void;
  onAdd: () => void;
  onRefresh: () => void;
  onHide: () => void;
}) {
  const [query, setQuery] = useState('');
  const [format, setFormat] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [nodeQuery, setNodeQuery] = useState('');
  useEffect(() => {
    setNodeQuery('');
    setCollapsed(new Set());
  }, [document?.source.version_id]);
  const files = artifacts.filter(
    (file) =>
      (!format || file.format === format) &&
      `${file.name} ${file.path}`.toLowerCase().includes(query.toLowerCase()),
  );
  const nodes: Data[] = document?.metadata?.nodes || [];
  const queryText = nodeQuery.trim().toLowerCase();
  const matches = nodes.filter((node) => node.path.toLowerCase().includes(queryText));
  function searchNodes(value: string) {
    setNodeQuery(value);
    const matches = nodes.filter((node) =>
      node.path.toLowerCase().includes(value.trim().toLowerCase()),
    );
    if (value.trim())
      setCollapsed(
        (old) =>
          new Set(
            [...old].filter(
              (path) =>
                !matches.some(
                  (node) =>
                    node.path !== path && node.path.startsWith(path === '/' ? '/' : path + '/'),
                ),
            ),
          ),
      );
  }
  return (
    <aside className="navigator" aria-label="Data navigator">
      <div className="panel-heading">
        <strong>
          Project files <span>{artifacts.length}</span>
        </strong>
        <div>
          <button
            className="icon-button"
            title="Refresh files"
            aria-label="Refresh files"
            onClick={onRefresh}
          >
            <Icon name="Refresh" />
          </button>
          <button className="icon-button" title="Add file" aria-label="Add file" onClick={onAdd}>
            <Icon name="Plus" />
          </button>
          <button
            className="icon-button"
            title="Hide data navigator"
            aria-label="Hide data navigator"
            onClick={onHide}
          >
            <Icon name="Close" />
          </button>
        </div>
      </div>
      <div className="navigator-filter">
        <Search value={query} onChange={setQuery} />
        <select
          aria-label="File format"
          value={format}
          onChange={(event) => setFormat(event.target.value)}
        >
          <option value="">All formats</option>
          <option>HDF5</option>
          <option>Parquet</option>
          <option>CSV</option>
        </select>
      </div>
      <div className="file-list" aria-label="Project files">
        {files.map((file) => (
          <button
            key={file.version_id}
            className={`file-row ${document?.source.version_id === file.version_id ? 'selected' : ''}`}
            aria-pressed={document?.source.version_id === file.version_id}
            onClick={() => onOpen(file)}
            title={file.path}
          >
            <FileType format={file.format} />
            <span>{file.name}</span>
            {file.stale && (
              <span className="changed-marker" title="Source changed or unavailable">
                !
              </span>
            )}
          </button>
        ))}
        {!files.length && (
          <p className="inline-empty">
            {artifacts.length ? 'No matching files.' : 'Add a local file to begin.'}
          </p>
        )}
      </div>
      <div className="panel-heading structure-heading">
        <strong>File structure</strong>
        {document && <span>{document.source.format}</span>}
      </div>
      {document && (
        <div className="structure-name" title={document.source.path}>
          {document.source.name}
        </div>
      )}
      {(nodes.length > 8 || nodeQuery) && (
        <Search label="Find a dataset" value={nodeQuery} onChange={searchNodes} />
      )}
      <div
        className="hierarchy"
        onKeyDown={(event) => {
          const buttons = Array.from(
            event.currentTarget.querySelectorAll<HTMLButtonElement>('button'),
          );
          const index = buttons.indexOf(event.target as HTMLButtonElement);
          if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
            buttons[
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? buttons.length - 1
                  : Math.max(
                      0,
                      Math.min(buttons.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)),
                    )
            ]?.focus();
          }
        }}
      >
        {nodes
          .filter(
            (node) =>
              (!queryText ||
                matches.some(
                  (match) =>
                    match.path === node.path ||
                    match.path.startsWith(node.path === '/' ? '/' : node.path + '/'),
                )) &&
              !Array.from(collapsed).some(
                (path) =>
                  node.path !== path && node.path.startsWith(path === '/' ? '/' : path + '/'),
              ),
          )
          .map((node) => (
            <button
              className={`node ${document?.controls.node === node.path ? 'selected' : ''}`}
              key={node.path}
              style={{
                paddingLeft: 8 + Math.max(0, node.path.split('/').filter(Boolean).length - 1) * 12,
              }}
              title={node.limitation || node.path}
              aria-pressed={document?.controls.node === node.path}
              aria-expanded={node.kind === 'group' ? !collapsed.has(node.path) : undefined}
              onClick={() => {
                if (node.kind === 'group') {
                  setCollapsed((old) => {
                    const next = new Set(old);
                    if (next.has(node.path)) next.delete(node.path);
                    else next.add(node.path);
                    return next;
                  });
                } else onNode(node.path);
              }}
            >
              <span className="node-symbol">
                {node.kind === 'group'
                  ? collapsed.has(node.path)
                    ? '▸'
                    : '▾'
                  : node.kind === 'link'
                    ? '↗'
                    : '▦'}
              </span>
              <span className="node-label">
                {node.path.split('/').pop() || '/'}
                {node.shape && <small>{node.shape.join(' × ')}</small>}
              </span>
              <span className="node-dtype">{node.dtype || node.kind}</span>
            </button>
          ))}
        {queryText && !matches.length && (
          <p className="inline-empty">
            No matching datasets. Clear the search to see the file structure.
          </p>
        )}
        {!document && <p className="inline-empty">Open a file to inspect its structure.</p>}
        {document?.metadata?.fields && (
          <table className="field-list">
            <thead>
              <tr>
                <th>Column</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {document.metadata.fields.map((field: Data) => (
                <tr key={field.name}>
                  <td>{field.name}</td>
                  <td>{field.dtype}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {document && !document.metadata && (
          <p className="inline-empty">
            {document.source.stale
              ? 'Source changed or unavailable.'
              : 'File structure is not loaded.'}
          </p>
        )}
        {document?.metadata && !document.metadata.coverage?.complete && (
          <p className="warning">Structure is incomplete. Narrow the path to inspect further.</p>
        )}
      </div>
      <div className="navigator-footer">
        <Icon name="Info" /> References only. Source files stay in place.
      </div>
    </aside>
  );
}
export function FileOverview({
  artifacts,
  onOpen,
  onAdd,
}: {
  artifacts: Data[];
  onOpen: (file: Data) => void;
  onAdd: () => void;
}) {
  const [query, setQuery] = useState('');
  const files = artifacts.filter((file) =>
    `${file.name} ${file.path}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <section className="file-overview">
      <div className="view-heading">
        <div>
          <h1>Project files</h1>
          <p>Open a file to read a bounded preview. Your source data remains unchanged.</p>
        </div>
        <button onClick={onAdd}>
          <Icon name="Plus" />
          Add file
        </button>
      </div>
      <Search label="Filter project files" value={query} onChange={setQuery} />
      {files.length ? (
        <div className="table-scroll library-table">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Format</th>
                <th>Source</th>
                <th>Condition</th>
              </tr>
            </thead>
            <tbody>
              {files.map((file) => (
                <tr key={file.version_id}>
                  <td>
                    <button className="file-link" onClick={() => onOpen(file)}>
                      <FileType format={file.format} />
                      {file.name}
                    </button>
                  </td>
                  <td>{file.format}</td>
                  <td className="path-cell" title={file.path}>
                    {file.path}
                  </td>
                  <td>
                    {file.stale ? (
                      <Badge value="STALE" />
                    ) : (
                      <span className="available">Available</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title={artifacts.length ? 'No matching files' : 'This project has no files yet'}>
          <p>Add an HDF5, Parquet or CSV file from an approved source folder.</p>
          <button onClick={onAdd}>Add file</button>
        </Empty>
      )}
    </section>
  );
}
type DocumentProps = {
  document: Document;
  busy: boolean;
  change: (patch: Partial<Controls>) => void;
  run: (operation: Operation, patch?: Partial<Request>) => void;
};
function OptionsNotice({ action }: { action: string }) {
  return (
    <div className="unapplied-options">
      <span>Options changed</span> The result shows the last applied scope. Use {action} to apply
      the edited options.
    </div>
  );
}
export function DataView({ document, busy, change, run }: DocumentProps) {
  const controls = document.controls;
  const advanced = controls.readOptions;
  const mode = controls.dataMode;
  const array = document.source.format === 'HDF5';
  const result = document.results[mode];
  const applied = result?.parameters;
  const columns = controls.columns
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
  const optionsChanged = Boolean(
    applied &&
    ((array
      ? controls.node !== applied.node ||
        controls.selection.trim() !== (applied.selection || '').trim()
      : JSON.stringify(columns) !== JSON.stringify(applied.columns || []) ||
        (mode === 'preview' && controls.offset !== (applied.offset || 0))) ||
      (mode === 'profile' && controls.maxRows !== applied.max_rows)),
  );
  return (
    <section className="data-view">
      <div className="operation-toolbar">
        <div className="segmented" aria-label="Data view">
          <button aria-pressed={mode === 'preview'} onClick={() => change({ dataMode: 'preview' })}>
            Preview
          </button>
          <button
            disabled={
              !document.results.profile && (busy || !document.metadata || document.source.stale)
            }
            aria-pressed={mode === 'profile'}
            onClick={() => {
              change({ dataMode: 'profile' });
              if (!document.results.profile) run('profile');
            }}
          >
            Statistics
          </button>
        </div>
        {array ? (
          <label className="inline-label">
            Slice
            <input
              aria-label="Array selection"
              value={controls.selection}
              onChange={(event) => change({ selection: event.target.value })}
              placeholder="Bounded default"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !busy) run(mode);
              }}
            />
          </label>
        ) : (
          <label className="inline-label">
            Columns
            <input
              aria-label="Preview columns"
              value={controls.columns}
              onChange={(event) => change({ columns: event.target.value })}
              placeholder="All supported columns"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !busy) run(mode);
              }}
            />
          </label>
        )}
        <button
          disabled={busy || document.source.stale || !document.metadata}
          onClick={() => run(mode)}
        >
          <Icon name="Refresh" />
          Update
        </button>
        <button
          className="quiet"
          aria-expanded={advanced}
          onClick={() => change({ readOptions: !advanced })}
        >
          <Icon name="Settings" />
          Read options
        </button>
      </div>
      {advanced && (
        <div className="read-options">
          {array && (
            <label>
              Dataset path
              <input
                value={controls.node}
                onChange={(event) => change({ node: event.target.value })}
                placeholder="/group/dataset"
              />
            </label>
          )}
          {(array || mode === 'preview') && (
            <label>
              {array ? 'Slice (indices or start:stop:step)' : 'Start row'}
              <input
                aria-label={array ? 'Explicit array slice' : 'Start row'}
                type={array ? 'text' : 'number'}
                min={array ? undefined : 0}
                value={array ? controls.selection : controls.offset}
                onChange={(event) =>
                  change(
                    array
                      ? { selection: event.target.value }
                      : { offset: Number(event.target.value) },
                  )
                }
                placeholder="0:32,0:32"
              />
            </label>
          )}
          <label>
            Profile row/cell budget
            <input
              type="number"
              min="1"
              max="1000000"
              value={controls.maxRows}
              onChange={(event) => change({ maxRows: Number(event.target.value) })}
            />
          </label>
          <p>
            Previews read at most 64 table rows or 10,000 array cells. CSV pagination rescans the
            prefix.
          </p>
        </div>
      )}
      {array && (
        <div className="dataset-location">
          <Icon name="Explore" />
          <code>{controls.node}</code>
          {!controls.selection && <span>Bounded preview</span>}
        </div>
      )}
      {result ? (
        <>
          {optionsChanged && <OptionsNotice action="Update" />}
          <ResultView
            result={result}
            representation={controls.arrayView}
            onRepresentationChange={(arrayView) => change({ arrayView })}
          />
          {mode === 'preview' && result.rows && (
            <div className="table-pagination">
              <span>
                {result.rows.length} rows shown · offset {result.offset ?? 0}
              </span>
              <button
                className="secondary"
                disabled={busy || (result.offset ?? 0) === 0}
                onClick={() => {
                  const offset = Math.max(0, (result.offset ?? 0) - 64);
                  change({ offset });
                  run('preview', { offset });
                }}
              >
                Previous rows
              </button>
              <button
                className="secondary"
                disabled={busy || result.next_offset == null}
                onClick={() => {
                  change({ offset: result.next_offset });
                  run('preview', { offset: result.next_offset });
                }}
              >
                Next rows
              </button>
            </div>
          )}
        </>
      ) : (
        <Empty
          icon="Explore"
          title={
            busy
              ? 'Reading selection…'
              : document.source.stale
                ? 'Source unavailable'
                : !document.metadata
                  ? 'File structure not loaded'
                  : array && controls.node === '/'
                    ? 'Select a dataset'
                    : 'No values read in this scope'
          }
        >
          <p>
            {!document.metadata && !busy
              ? 'Read the file structure to identify available datasets and columns.'
              : array && controls.node === '/'
                ? 'Choose a supported numeric dataset in the navigator. Read options also accept an explicit path.'
                : busy
                  ? 'The requested scope is being read in a cancellable worker.'
                  : 'Use Update to read this selection.'}
          </p>
          {!document.metadata && !busy && !document.source.stale && (
            <button
              onClick={() => run('inspect', { node: '/', selection: '', columns: [], offset: 0 })}
            >
              Read structure
            </button>
          )}
        </Empty>
      )}
    </section>
  );
}
export function CompareView({
  document,
  artifacts,
  busy,
  change,
  run,
}: DocumentProps & { artifacts: Data[] }) {
  const controls = document.controls;
  const array = document.source.format === 'HDF5';
  const compatible = artifacts.filter(
    (file) =>
      file.path !== document.source.path &&
      (array ? file.format === 'HDF5' : file.format !== 'HDF5'),
  );
  const reference = compatible.find((file) => file.path === controls.other);
  const keys = controls.keys
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
  const ready = Boolean(
    reference &&
    !reference.stale &&
    (array ? controls.node !== '/' : keys.length || controls.position),
  );
  const addKey = (name: string) =>
    change({
      keys: (keys.includes(name) ? keys.filter((key) => key !== name) : [...keys, name]).join(','),
    });
  const scope = (value: Controls) => [
    value.other,
    value.maxRows,
    Number(value.atol),
    Number(value.rtol),
    value.convertUnits,
    value.nanEqual,
    ...(array
      ? [value.node, value.selection.trim()]
      : [
          value.position,
          value.position
            ? []
            : value.keys
                .split(',')
                .map((key) => key.trim())
                .filter(Boolean),
          value.columns
            .split(',')
            .map((column) => column.trim())
            .filter(Boolean),
        ]),
  ];
  const applied = document.appliedControls.diff;
  return (
    <section className="analysis-view">
      <form
        className="analysis-controls"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready && !busy)
            run('diff', {
              other: controls.other,
              keys: controls.position ? [] : keys,
              position: controls.position,
              atol: Number(controls.atol),
              rtol: Number(controls.rtol),
              convert_units: controls.convertUnits,
              nan_equal: controls.nanEqual,
            });
        }}
      >
        <div className="source-pair">
          <div>
            <span>Source A</span>
            <strong>{document.source.name}</strong>
          </div>
          <Icon name="Compare" />
          <label>
            Reference B
            <select
              value={controls.other}
              onChange={(event) => change({ other: event.target.value })}
            >
              <option value="">Choose a compatible file</option>
              {compatible.map((file) => (
                <option key={file.version_id} value={file.path} disabled={file.stale}>
                  {file.name}
                  {file.stale ? ' (unavailable)' : ''}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="parameter-grid">
          {array ? (
            <>
              <label>
                Dataset
                <input
                  aria-label="Comparison dataset path"
                  value={controls.node}
                  onChange={(event) => change({ node: event.target.value })}
                />
              </label>
              <label>
                Selection
                <input
                  aria-label="Comparison selection"
                  value={controls.selection}
                  onChange={(event) => change({ selection: event.target.value })}
                  placeholder="Full dataset within budget"
                />
              </label>
            </>
          ) : (
            <>
              <label>
                Alignment
                <select
                  aria-label="Table alignment"
                  value={controls.position ? 'position' : 'keys'}
                  onChange={(event) => change({ position: event.target.value === 'position' })}
                >
                  <option value="keys">Unique non-null keys</option>
                  <option value="position">Explicit row position</option>
                </select>
              </label>
              <label>
                Alignment keys
                <input
                  disabled={controls.position}
                  value={controls.keys}
                  onChange={(event) => change({ keys: event.target.value })}
                  placeholder="Select columns below or enter names"
                />
              </label>
            </>
          )}
          <label>
            Absolute tolerance
            <input
              type="number"
              min="0"
              step="any"
              value={controls.atol}
              onChange={(event) => change({ atol: event.target.value })}
            />
          </label>
          <label>
            Relative tolerance
            <input
              type="number"
              min="0"
              step="any"
              value={controls.rtol}
              onChange={(event) => change({ rtol: event.target.value })}
            />
          </label>
        </div>
        {!array && !controls.position && document.metadata?.fields && (
          <div className="key-picker" aria-label="Available alignment columns">
            {document.metadata.fields.map((field: Data) => (
              <button
                className="key-chip"
                type="button"
                key={field.name}
                aria-pressed={keys.includes(field.name)}
                onClick={() => addKey(field.name)}
              >
                {keys.includes(field.name) && <Icon name="Check" />}
                {field.name}
              </button>
            ))}
          </div>
        )}
        <div className="analysis-options">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={controls.convertUnits}
              onChange={(event) => change({ convertUnits: event.target.checked })}
            />
            Convert compatible units
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={controls.nanEqual}
              onChange={(event) => change({ nanEqual: event.target.checked })}
            />
            Paired NaNs equal
          </label>
          <details>
            <summary>Columns and scan budget</summary>
            <div className="parameter-grid">
              <label>
                Columns
                <input
                  value={controls.columns}
                  onChange={(event) => change({ columns: event.target.value })}
                  placeholder="All supported columns"
                />
              </label>
              <label>
                Row/cell budget
                <input
                  type="number"
                  min="1"
                  max="1000000"
                  value={controls.maxRows}
                  onChange={(event) => change({ maxRows: Number(event.target.value) })}
                />
              </label>
            </div>
          </details>
        </div>
        <div className="submit-row">
          <p>
            {!reference
              ? 'Choose a reference file to compare.'
              : !ready
                ? 'Choose alignment keys or explicitly use row position.'
                : 'Tolerance: abs(A − B) ≤ atol + rtol × abs(B). No inferred alignment.'}
          </p>
          <button disabled={busy || document.source.stale || !ready} type="submit">
            <Icon name="Compare" />
            Run comparison
          </button>
        </div>
      </form>
      {document.results.diff ? (
        <>
          {applied && JSON.stringify(scope(controls)) !== JSON.stringify(scope(applied)) && (
            <OptionsNotice action="Run comparison" />
          )}
          <ResultView result={document.results.diff} />
        </>
      ) : (
        <div className="analysis-placeholder">
          Comparison findings will appear here. Complete coverage and bounded examples stay with the
          saved report.
        </div>
      )}
    </section>
  );
}
export function ValidateView({
  document,
  contracts,
  busy,
  change,
  run,
  onPickContract,
}: DocumentProps & { contracts: Data[]; onPickContract?: () => void }) {
  const controls = document.controls;
  const contract = contracts.find((item) => item.path === controls.contract);
  const [local, setLocal] = useState(false);
  return (
    <section className="analysis-view">
      <form
        className="analysis-controls"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && controls.contract) run('validate', { contract: controls.contract });
        }}
      >
        <div className="validation-controls">
          <label>
            Data contract
            <select
              value={contract ? controls.contract : ''}
              onChange={(event) => change({ contract: event.target.value })}
            >
              <option value="">Choose a project contract</option>
              {contracts.map((item) => (
                <option key={item.path} value={item.path} disabled={item.invalid}>
                  {item.name}
                  {item.invalid ? ' (invalid)' : ` · revision ${item.revision}`}
                </option>
              ))}
            </select>
          </label>
          <label>
            Row/cell budget
            <input
              type="number"
              min="1"
              max="1000000"
              value={controls.maxRows}
              onChange={(event) => change({ maxRows: Number(event.target.value) })}
            />
          </label>
          <button disabled={busy || document.source.stale || !controls.contract} type="submit">
            <Icon name="Validate" />
            Run validation
          </button>
        </div>
        <button
          type="button"
          className="text-button"
          aria-expanded={local}
          onClick={() => setLocal(!local)}
        >
          Use a local contract file
        </button>
        {local && (
          <div className="local-contract">
            <label>
              Local contract path
              <input
                value={controls.contract}
                onChange={(event) => change({ contract: event.target.value })}
                placeholder="Path inside an approved source folder"
              />
            </label>
            {onPickContract && (
              <button className="secondary" type="button" onClick={onPickContract}>
                Choose contract file
              </button>
            )}
          </div>
        )}
        {contract?.rules && (
          <details className="contract-rules">
            <summary>{contract.rules.length} declared rules · inspect contract</summary>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Rule</th>
                    <th>Field / path</th>
                    <th>Definition</th>
                  </tr>
                </thead>
                <tbody>
                  {contract.rules.map((rule: Data) => (
                    <tr key={rule.id}>
                      <td>{rule.kind}</td>
                      <td>{rule.field || rule.node || '—'}</td>
                      <td>
                        <code>{JSON.stringify(rule)}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
        <p className="scope-note">
          PASS requires complete requested evidence. A witnessed violation can FAIL with incomplete
          counts; missing evidence remains INCONCLUSIVE.
        </p>
      </form>
      {document.results.validate ? (
        <>
          {document.appliedControls.validate &&
            (controls.contract !== document.appliedControls.validate.contract ||
              controls.maxRows !== document.appliedControls.validate.maxRows) && (
              <OptionsNotice action="Run validation" />
            )}
          <ResultView result={document.results.validate} />
        </>
      ) : (
        <div className="analysis-placeholder">
          Validation findings will appear here, with coverage and evidence for every rule.
        </div>
      )}
    </section>
  );
}
export function Inspector({
  document,
  onRuns,
  onLineage,
  onHide,
}: {
  document: Document;
  onRuns: () => void;
  onLineage: () => void;
  onHide: () => void;
}) {
  const node = document.metadata?.nodes?.find((item: Data) => item.path === document.controls.node);
  return (
    <aside className="inspector" aria-label="Selection inspector">
      <div className="panel-heading">
        <strong>Selection inspector</strong>
        <button
          className="icon-button"
          title="Hide selection inspector"
          aria-label="Hide selection inspector"
          onClick={onHide}
        >
          <Icon name="Close" />
        </button>
      </div>
      <dl>
        <dt>Format</dt>
        <dd>{document.source.format}</dd>
        <dt>Dataset</dt>
        <dd>{document.source.format === 'HDF5' ? document.controls.node : 'Table'}</dd>
        <dt>Type</dt>
        <dd>
          {node?.dtype ||
            (document.metadata?.fields ? `${document.metadata.fields.length} columns` : '—')}
        </dd>
        <dt>Shape</dt>
        <dd>{node?.shape?.join(' × ') || '—'}</dd>
        <dt>Unit</dt>
        <dd>{display(node?.unit || node?.attributes?.unit || 'Unspecified')}</dd>
      </dl>
      <details>
        <summary>Source identity</summary>
        <dl>
          <dt>Version</dt>
          <dd>
            <code>{document.source.version_id}</code>
          </dd>
          <dt>Evidence</dt>
          <dd>
            {document.source.identity_strength === 'sha256'
              ? 'SHA-256 fingerprint'
              : 'Size and timestamps (weak)'}
          </dd>
          <dt>Consistency</dt>
          <dd>Best effort; no snapshot guarantee</dd>
          <dt>Source path</dt>
          <dd className="path-value">{document.source.current_path || document.source.path}</dd>
        </dl>
      </details>
      {node?.limitation && <p className="warning">{node.limitation}</p>}
      {node?.attributes && (
        <details>
          <summary>Dataset attributes</summary>
          <dl>
            {Object.entries(node.attributes).map(([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{display(value)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      <details>
        <summary>Available operations</summary>
        <p>{document.metadata?.capabilities?.join(', ') || 'Structure has not been read.'}</p>
      </details>
      <div className="inspector-actions">
        <button className="secondary" onClick={onRuns}>
          <Icon name="Runs" />
          Related runs
        </button>
        <button className="secondary" onClick={onLineage}>
          <Icon name="Lineage" />
          Dependencies
        </button>
      </div>
      <p className="scope-note">
        A preview covers its requested selection. It cannot establish whole-file validity.
      </p>
    </aside>
  );
}
