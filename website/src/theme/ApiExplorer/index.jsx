import React from "react";
import CodeBlock from "@theme/CodeBlock";

export default function ApiExplorer({ item }) {
  return (item["x-codeSamples"] ?? []).map(({ label, source }) => (
    <CodeBlock key={source} language="bash" title={label}>
      {source}
    </CodeBlock>
  ));
}
