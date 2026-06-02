import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import i18n from '../i18n';

interface LanguageState {
  language: 'en' | 'zh';
  setLanguage: (lang: 'en' | 'zh') => void;
  toggleLanguage: () => void;
}

export const useLanguageStore = create<LanguageState>()(
  persist(
    (set, get) => ({
      language: (i18n.language as 'en' | 'zh') || 'zh',

      setLanguage: (lang: 'en' | 'zh') => {
        i18n.changeLanguage(lang);
        set({ language: lang });
      },

      toggleLanguage: () => {
        const currentLang = get().language;
        const newLang = currentLang === 'en' ? 'zh' : 'en';
        i18n.changeLanguage(newLang);
        set({ language: newLang });
      }
    }),
    {
      name: 'marcus-language-storage'
    }
  )
);