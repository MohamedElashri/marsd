import { useEffect, useRef, type ReactNode } from 'react';

export function Mark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path d="M4 24V8h5l7 12 7-12h5v16" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="M4 29h24" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}
export function Brand() {
  return (
    <div className="brand">
      <Mark />
      <span>Marsd</span>
      <span lang="ar">مرصد</span>
    </div>
  );
}
const paths = {
  Files: 'M3 7V4h7l2 3h9v13H3z',
  Explore: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  Compare: 'M8 3v18 M16 3v18 M3 8l5-5 5 5 M11 16l5 5 5-5',
  Validate: 'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',
  Runs: 'M8 5l12 7-12 7z',
  Lineage: 'M4 4h5v5H4z M15 15h5v5h-5z M6.5 9v8.5H15 M9 6.5h8.5V15',
  Reports: 'M5 3h10l4 4v14H5z M14 3v5h5 M8 12h8 M8 16h6',
  Settings: 'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6',
  Help: 'M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3 M12 17v.1 M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20',
  Search: 'M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15 M16 16l5 5',
  Plus: 'M12 5v14 M5 12h14',
  Close: 'M6 6l12 12 M18 6L6 18',
  Check: 'M5 12l4 4 10-10',
  Refresh: 'M20 10a8 8 0 1 0-2 8 M20 3v7h-7',
  Info: 'M12 11v6 M12 7v.1 M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20',
} as const;
export function Icon({ name }: { name: keyof typeof paths }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
export function Badge({ value }: { value: string }) {
  return <span className={`badge ${value.toLowerCase()}`}>{value.replaceAll('_', ' ')}</span>;
}
export function FileType({ format }: { format: string }) {
  return (
    <span className={`file-type ${format.toLowerCase()}`}>
      {format === 'HDF5'
        ? 'H5'
        : format === 'Parquet'
          ? 'PQ'
          : format === 'CSV'
            ? 'CSV'
            : format.slice(0, 3)}
    </span>
  );
}
export function Search({
  value,
  onChange,
  label = 'Find files',
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
}) {
  return (
    <label className="search-field">
      <Icon name="Search" />
      <input
        type="search"
        aria-label={label}
        placeholder={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
export function Empty({
  icon = 'Files',
  title,
  children,
}: {
  icon?: keyof typeof paths;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Icon name={icon} />
      <h2>{title}</h2>
      {children}
    </div>
  );
}
export function Dialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="dialog"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="dialog-heading">
        <h2>{title}</h2>
        <button
          className="icon-button"
          aria-label={`Close ${title.toLowerCase()}`}
          onClick={onClose}
        >
          <Icon name="Close" />
        </button>
      </div>
      {children}
    </dialog>
  );
}
