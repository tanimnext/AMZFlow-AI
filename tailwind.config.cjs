/** AmzFlow AI — brand palette mirrors web_app/static/theme.css.
 *  Rebuild after ANY template edit:  npm run build:css
 *  (utility classes used in a template but absent from tailwind.min.css are
 *  silently no-ops, which is how the v6 build kept drifting.) */
module.exports = {
  content: ["./web_app/templates/**/*.html", "./web_app/static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#fff7ed",
          100: "#ffedd5",
          200: "#fed7aa",
          400: "#fb923c",
          500: "#f97316",
          600: "#ea580c",
          700: "#c2410c",
        },
        ok: {
          50: "#f0fdf4",
          100: "#dcfce7",
          200: "#bbf7d0",
          500: "#22c55e",
          600: "#16a34a",
          700: "#15803d",
        },
        ink: {
          400: "#94a3b8",
          500: "#64748b",
          600: "#475569",
          900: "#0f172a",
        },
        line: "#e2e8f0",
        surface: {
          DEFAULT: "#ffffff",
          2: "#f8fafc",
          3: "#f1f5f9",
        },
      },
      borderRadius: { xl2: "16px" },
      boxShadow: {
        card: "0 1px 2px rgba(15,23,42,.04), 0 4px 16px rgba(15,23,42,.06)",
        pop: "0 8px 32px rgba(15,23,42,.14)",
      },
    },
  },
  plugins: [],
};
