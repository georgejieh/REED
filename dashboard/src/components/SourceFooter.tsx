import type { Source } from "../lib/types";

export interface SourceFooterProps {
  sources: Source[];
}

/** Renders the numbered sources list at the bottom of the reader. */
export function SourceFooter({ sources }: SourceFooterProps) {
  return (
    <footer className="source-footer">
      <h3>Sources</h3>
      <ol className="source-grid">
        {sources.map((source) => (
          <li key={source.id}>
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              {source.id}. {source.name}
            </a>
          </li>
        ))}
      </ol>
    </footer>
  );
}
