import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type Theme = 'dark' | 'light';

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: 'dark',

      setTheme: (theme: Theme) => {
        set({ theme });
      },

      toggleTheme: () => {
        const current = get().theme;
        const next: Theme = current === 'dark' ? 'light' : 'dark';
        set({ theme: next });
      },
    }),
    {
      name: 'marcus-theme-storage',
    }
  )
);
