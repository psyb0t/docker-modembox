import endpoints from "./.content/api/sidebar.ts";

export default {
  docs: [
    "index",
    "install",
    "console",
    "devices",
    {
      type: "category",
      label: "API",
      link: { type: "doc", id: "api" },
      items: endpoints,
    },
    "mcp",
    "hooks",
  ],
};
