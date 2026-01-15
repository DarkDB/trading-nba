/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["class"],
    content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html"
  ],
  theme: {
    extend: {
      fontFamily: {
        headings: ['Barlow Condensed', 'sans-serif'],
        body: ['Manrope', 'sans-serif'],
        data: ['JetBrains Mono', 'monospace'],
      },
      borderRadius: {
        lg: '0.5rem',
        md: '0.375rem',
        sm: '0.25rem'
      },
      colors: {
        background: '#09090B',
        foreground: '#FAFAFA',
        card: {
          DEFAULT: '#18181B',
          foreground: '#FAFAFA'
        },
        popover: {
          DEFAULT: '#18181B',
          foreground: '#FAFAFA'
        },
        primary: {
          DEFAULT: '#22C55E',
          foreground: '#FFFFFF'
        },
        secondary: {
          DEFAULT: '#3B82F6',
          foreground: '#FFFFFF'
        },
        muted: {
          DEFAULT: '#27272A',
          foreground: '#71717A'
        },
        accent: {
          DEFAULT: '#27272A',
          foreground: '#FAFAFA'
        },
        destructive: {
          DEFAULT: '#EF4444',
          foreground: '#FFFFFF'
        },
        warning: {
          DEFAULT: '#EAB308',
          foreground: '#000000'
        },
        border: '#27272A',
        input: '#27272A',
        ring: '#22C55E',
        chart: {
          '1': '#22C55E',
          '2': '#3B82F6',
          '3': '#EAB308',
          '4': '#EF4444',
          '5': '#71717A'
        }
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' }
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' }
        }
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out'
      }
    }
  },
  plugins: [require("tailwindcss-animate")],
};
