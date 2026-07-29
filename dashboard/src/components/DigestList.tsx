import { formatDateTime, formatLabel } from "../lib/format";
import type { Digest } from "../lib/types";

export interface DigestListProps {
  digests: Digest[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function DigestList({ digests, selectedId, onSelect }: DigestListProps) {
  return (
    <section className="digest-list" aria-label="Digest list">
      <header className="pane-header">
        <div>
          <p className="eyebrow">PUBLISHED ARCHIVE</p>
          <h2>Digests</h2>
        </div>
        <span className="count-badge" aria-label={`${digests.length} digests`}>
          {String(digests.length).padStart(2, "0")}
        </span>
      </header>
      {digests.length === 0 ? (
        <div className="empty-state">
          <strong>No published digests</strong>
          <p>
            Complete setup, then run an enabled market window. Failed and
            partial drafts never appear here.
          </p>
        </div>
      ) : (
        <div role="list" className="digest-items">
          {digests.map((digest) => (
            <div role="listitem" className="digest-item" key={digest.id}>
              <button
                id={`digest-${digest.id}`}
                type="button"
                aria-current={selectedId === digest.id ? "true" : undefined}
                onClick={() => onSelect(digest.id)}
              >
                <span className="item-window">
                  {formatLabel(digest.market_window)}
                </span>
                <span className="item-headline">{digest.title}</span>
                <time className="item-time" dateTime={digest.published_at}>
                  {formatDateTime(digest.published_at)}
                </time>
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
