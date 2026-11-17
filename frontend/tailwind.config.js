/** Paleta Goltrader (Wemerson): #121318 fundo, #191C23 painéis, #1A1C23 inputs, #212A34 superfícies, #646C78 apoio, #40D7DD destaque. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        gt: { bg: '#121318', panel: '#191C23', input: '#1A1C23', surface: '#212A34', muted: '#646C78', accent: '#40D7DD' },
      },
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'] },
    },
  },
  plugins: [],
};
