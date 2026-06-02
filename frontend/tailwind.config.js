/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
    "./node_modules/@earendil-works/pi-web-ui/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#f0f9ff',
          100: '#e0f2fe',
          200: '#bae6fd',
          300: '#7dd3fc',
          400: '#38bdf8',
          500: '#0ea5e9',
          600: '#0284c7',
          700: '#0369a1',
          800: '#075985',
          900: '#0c4a6e',
        },
        dark: {
          100: '#1e293b',
          200: '#1a2234',
          300: '#151c2c',
          400: '#0d1117',
        },
        // 深海蓝品牌色系
        'deepsea': {
          50: '#EEF1FA',
          100: '#D4DBF2',
          200: '#A9B7E5',
          300: '#7E93D8',
          400: '#536FCB',
          500: '#284BBE',
          600: '#122E8A',
          700: '#0E2470',
          800: '#0B1B56',
          900: '#07123C',
        },
        // 柔奶白系
        'cream': {
          50: '#FDFAF7',
          100: '#F5EFEA',
          200: '#EDE6DD',
          300: '#E6DDD3',
          400: '#DFD4C9',
          500: '#D8CBBF',
          600: '#C4B5A5',
          700: '#A89A8A',
          800: '#8C7F6F',
          900: '#706454',
        },
      }
    },
  },
  plugins: [],
}
