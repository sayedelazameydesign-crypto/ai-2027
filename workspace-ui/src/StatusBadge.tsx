import { type CapabilityStatus, statusLabel, type Language } from './capabilities';

const STATUS_ICON: Record<CapabilityStatus, string> = {
  implemented: '✓',
  partial: '◐',
  planned: '◔',
  unavailable: '✕',
  blocked: '⛔',
};

export function StatusBadge({ status, lang }: { status: CapabilityStatus; lang: Language }) {
  return (
    <span className={`status-badge status-${status}`} title={statusLabel(status, lang)}>
      <span className="status-badge-icon" aria-hidden="true">{STATUS_ICON[status]}</span>
      <span>{statusLabel(status, lang)}</span>
    </span>
  );
}
