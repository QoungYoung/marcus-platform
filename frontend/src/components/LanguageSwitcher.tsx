import { useTranslation } from 'react-i18next';
import { useLanguageStore } from '../store/languageStore';
import { Globe } from 'lucide-react';

export function LanguageSwitcher() {
  const { t } = useTranslation();
  const { language, toggleLanguage } = useLanguageStore();

  return (
    <button
      onClick={toggleLanguage}
      className="flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs font-medium transition-all"
      style={{
        color: 'var(--topnav-link-color)',
        background: 'rgba(255,255,255,0.06)',
        border: '1px solid var(--topnav-border)',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = 'var(--topnav-link-active-color)';
        e.currentTarget.style.background = 'var(--topnav-link-active-bg)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = 'var(--topnav-link-color)';
        e.currentTarget.style.background = 'rgba(255,255,255,0.06)';
      }}
      title={language === 'en' ? t('language.switchToChinese') : t('language.switchToEnglish')}
    >
      <Globe className="w-3.5 h-3.5" />
      <span>{language === 'en' ? '中' : 'EN'}</span>
    </button>
  );
}
