import type { Digest } from "../lib/types";

export interface DigestListProps {
  digests: Digest[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

interface DateGroup {
  date: string;
  items: Digest[];
}

function groupByDate(digests: Digest[]): DateGroup[] {
  const map = new Map<string, Digest[]>();
  for (const digest of digests) {
    const date = digest.as_of.slice(0, 10);
    const list = map.get(date) ?? [];
    list.push(digest);
    map.set(date, list);
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => (a > b ? -1 : 1))
    .map(([date, items]) => ({ date, items }));
}

/** Renders the left rail of selectable digest summaries grouped by date. */
export function DigestList({ digests, selectedId, onSelect }: DigestListProps) {
  const groups = groupByDate(digests);

  return (
    <section className="digest-list" aria-label="Digest list">
      <h1>REED</h1>
      {groups.length === 0 ? (
        <p className="empty">No digests yet.</p>
      ) : (
        <div role="list">
          {groups.map((group) => (
            <div key={group.date}>
              <header className="date-header">{group.date}</header>
              {group.items.map((digest) => (
                <button
                  key={digest.id ?? digest.as_of}
                  type="button"
                  role="listitem"
                  aria-current={selectedId === digest.id ? "true" : undefined}
                  data-testid="digest-list-item"
                  data-digest-id={digest.id}
                  onClick={digest.id ? () => {
                    if (digest.id) onSelect(digest.id);
                  } : undefined}
                >
                  <span className="item-headline">{digest.headline}</span>
                  <time className="item-time" dateTime={digest.as_of}>
                    {digest.as_of}
                  </time>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
