import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { accountsApi } from '../api/client';

export interface AccountOption {
  account_id: string;
  name: string;
  module?: string;
  initial_capital: number;
  available_cash: number;
}

interface AccountState {
  accounts: AccountOption[];
  activeAccount: string;
  loadAccounts: () => Promise<void>;
  setActiveAccount: (account: string) => void;
}

/** 全局模拟盘账户（顶部导航栏切换，所有页面共享） */
export const useAccountStore = create<AccountState>()(
  persist(
    (set, get) => ({
      accounts: [],
      activeAccount: 'stock',

      loadAccounts: async () => {
        try {
          const res = await accountsApi.list();
          const accounts: AccountOption[] = Array.isArray(res.data) ? res.data : [];
          set({ accounts });
          const current = get().activeAccount;
          if (accounts.length > 0 && !accounts.some((a) => a.account_id === current)) {
            set({ activeAccount: accounts[0].account_id });
          }
        } catch {
          // 静默失败，保持默认 stock
        }
      },

      setActiveAccount: (account: string) => set({ activeAccount: account }),
    }),
    { name: 'marcus-account-storage' }
  )
);