// `next lint` was removed in Next 16, so `npm run lint` runs eslint directly
// and needs a real flat config - there was none in the repo before.
// eslint-config-next 16 ships native flat configs, so no compat layer.
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", "src/types/api.generated.ts"],
  },
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // Backs the rule that a SQL Server password never reaches a log.
      "no-console": ["warn", { allow: ["warn", "error"] }],
      // One API client, so error handling stays in one place.
      "no-restricted-syntax": [
        "error",
        {
          selector: "NewExpression[callee.name='XMLHttpRequest']",
          message: "Use src/lib/api/uploads.ts so upload errors surface consistently.",
        },
      ],
    },
  },
  {
    files: ["src/lib/api/**"],
    rules: { "no-restricted-syntax": "off" },
  },
];

export default config;
