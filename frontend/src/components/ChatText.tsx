import { Fragment } from "react";

// Renders WhatsApp-style text (*bold*, _italic_, line breaks) as React nodes, never as raw HTML.
const TOKEN = /(\*[^*\n]+\*|_[^_\n]+_)/g;

function inline(line: string) {
  return line.split(TOKEN).map((part, i) => {
    if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) return <strong key={i}>{part.slice(1, -1)}</strong>;
    if (part.length > 2 && part.startsWith("_") && part.endsWith("_")) return <em key={i} className="text-ink-2">{part.slice(1, -1)}</em>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export function ChatText({ text }: { text: string }) {
  return (
    <div className="space-y-1 text-sm leading-relaxed">
      {text.split("\n").map((line, i) => (line.trim() === "" ? <div key={i} className="h-2" /> : <p key={i}>{inline(line)}</p>))}
    </div>
  );
}
