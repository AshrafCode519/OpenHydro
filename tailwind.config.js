// Tailwind for the dashboard: `npm run build:css` writes openhydro/web/css/tailwind.css (committed,
// so the app and its installer need no Node.js). CI fails if that file is out of date.
// Only complete class names are found: never build one from pieces ('text-' + color).
module.exports = {
  content: ['openhydro/web/*.html', 'openhydro/web/js/**/*.js'],
  darkMode: 'class',
  theme: {
    extend: {
      // The header shows every button's label on one line from here (they need about 1700px)
      screens: {
        wide: '1800px',
      },
      colors: {
        hydro: {
          50: '#ecfeff',
          400: '#22d3ee',
          500: '#06b6d4',
          600: '#0891b2',
          900: '#164e63',
        },
      },
    },
  },
};
