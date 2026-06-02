import { useState } from 'react';
import AgentSidebar from '../components/AgentSidebar';
import ChatContainer from '../components/ChatContainer';
import StockDetailPanel, { type StockInfo } from '../components/StockDetailPanel';

export default function TradingAgentPage() {
  const [selectedStock, setSelectedStock] = useState<StockInfo | null>(null);

  const handleStockSelect = (stock: StockInfo) => {
    console.log('handleStockSelect called with:', stock);
    setSelectedStock(stock);
    console.log('selectedStock state:', selectedStock);
  };

  return (
    <div className="agent-page-layout" style={layoutStyle}>
      {/* Left Sidebar */}
      <AgentSidebar
        onStockSelect={handleStockSelect}
        selectedSymbol={selectedStock?.symbol}
      />

      {/* Center Chat Container */}
      <div className="chat-wrapper">
        <ChatContainer onStockSelect={handleStockSelect} />
      </div>

      {/* Right Panel */}
      <StockDetailPanel stock={selectedStock} />

      <style>{`
        .agent-page-layout {
          width: 100%;
          flex: 1;
          min-height: 0;
          display: flex;
          overflow: hidden;
          background: var(--agent-bg-deep);
        }

        .chat-wrapper {
          flex: 1;
          display: flex;
          justify-content: center;
          align-items: stretch;
          padding: 20px;
          min-width: 0;
          overflow: hidden;
        }

        @keyframes pulse-dot {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.8); }
        }

        /* Chat panel styling */
        #trading-agent-container pi-chat-panel {
          background: transparent !important;
          height: 100% !important;
          flex: 1;
          display: flex;
          flex-direction: column;
        }

        #trading-agent-container message-list {
          background: var(--agent-bg-main) !important;
          flex: 1 1 auto !important;
          overflow-y: auto !important;
          padding: 16px !important;
          min-height: 0 !important;
        }

        /* Ensure the scroll container works */
        #trading-agent-container pi-chat-panel {
          background: transparent !important;
          height: 100% !important;
          flex: 1 !important;
          display: flex !important;
          flex-direction: column !important;
          overflow: hidden !important;
          min-height: 0 !important;
          border: none !important;
          box-shadow: none !important;
        }

        /* 强制覆盖 pi-web-ui 内部容器暗色背景 (Tailwind 类) */
        html[data-theme="light"] #trading-agent-container .bg-gray-900,
        html[data-theme="light"] #trading-agent-container .bg-gray-800,
        html[data-theme="light"] #trading-agent-container .bg-black {
          background-color: transparent !important;
        }

        /* Target the internal scrollable part if different */
        #trading-agent-container message-list::part(scroll) {
          overflow-y: auto !important;
        }

        #trading-agent-container user-message > div {
          display: flex !important;
          justify-content: flex-end !important;
        }

        #trading-agent-container user-message .user-message-container,
        #trading-agent-container user-message > div > div:first-child {
          background: var(--agent-user-msg-bg) !important;
          color: var(--agent-user-msg-text) !important;
          border: none !important;
          border-bottom-right-radius: 6px !important;
          border-right: 3px solid var(--agent-gold) !important;
          border-radius: 18px 18px 4px 18px !important;
          padding: 12px 16px !important;
          max-width: 80% !important;
          box-shadow: var(--agent-shadow-msg) !important;
        }

        /* 用户气泡内子元素强制亮色（覆盖全局 .text-foreground） */
        #trading-agent-container user-message .text-foreground,
        #trading-agent-container user-message .markdown-content {
          color: var(--agent-user-msg-text) !important;
        }

        #trading-agent-container assistant-message > div {
          display: flex !important;
          flex-direction: column !important;
          align-items: flex-start !important;
        }

        /* Assistant 消息气泡样式（内置组件渲染的文本块+工具调用块） */
        #trading-agent-container assistant-message > div > div {
          background: var(--agent-bg-bubble) !important;
          color: var(--agent-text-primary) !important;
          border: none !important;
          border-left: 3px solid var(--agent-gold) !important;
          border-radius: 18px 18px 18px 4px !important;
          padding: 14px 18px !important;
          max-width: 85% !important;
          box-shadow: var(--agent-shadow-msg) !important;
        }

        /* Token 用量信息 — 显示在气泡底部 */
        #trading-agent-container assistant-message .text-xs.text-muted-foreground {
          font-size: 10px !important;
          color: var(--agent-text-extra-dim) !important;
          padding: 0 18px 6px !important;
          opacity: 0.6;
        }

        /* 工具调用卡片样式 */
        #trading-agent-container tool-message > div {
          background: var(--agent-tool-card-bg) !important;
          border: 1px solid var(--agent-border-light) !important;
          border-radius: 10px !important;
          color: var(--agent-text-primary) !important;
        }

        #trading-agent-container tool-message .text-muted-foreground {
          color: var(--agent-text-secondary) !important;
        }

        #trading-agent-container tool-message .text-foreground {
          color: var(--agent-gold) !important;
        }

        #trading-agent-container tool-message .text-green-600,
        #trading-agent-container tool-message .dark\\:text-green-500 {
          color: var(--agent-green) !important;
        }

        #trading-agent-container tool-message .text-destructive {
          color: var(--agent-red) !important;
        }

        /* 工具调用内的代码块 */
        #trading-agent-container tool-message code-block,
        #trading-agent-container tool-message code-block pre,
        #trading-agent-container tool-message code-block code {
          color: var(--agent-text-secondary) !important;
        }
        /* 覆盖语法高亮变量，避免深色关键字不可见 */
        #trading-agent-container tool-message code-block {
          --syntax-keyword: #f0b90b;
          --syntax-string: #98c379;
          --syntax-number: #d19a66;
          --syntax-constant: #56b6c2;
          --syntax-function: #61afef;
          --syntax-comment: #5c6370;
          --syntax-property: #e06c75;
          --syntax-selector: #56b6c2;
          --syntax-operator: #abb2bf;
          --agent-text-extra-dim: #98c379;
          display: block !important;
          background: var(--agent-code-block-bg) !important;
          border-radius: 8px !important;
          padding: 10px 12px !important;
          font-size: 12px !important;
          overflow-x: auto !important;
          margin-top: 4px !important;
        }

        /* Markdown block styling */
        #trading-agent-container markdown-block {
          display: block !important;
          line-height: 1.6 !important;
          color: var(--agent-text-primary) !important;
        }

        #trading-agent-container markdown-block p {
          margin: 0 0 8px 0 !important;
        }

        #trading-agent-container markdown-block p:last-child {
          margin-bottom: 0 !important;
        }

        #trading-agent-container markdown-block code {
          background: var(--agent-code-inline-bg) !important;
          color: var(--agent-gold) !important;
          padding: 2px 6px !important;
          border-radius: 4px !important;
          font-family: 'SF Mono', 'JetBrains Mono', 'Consolas', monospace !important;
          font-size: 13px !important;
        }

        #trading-agent-container markdown-block pre {
          background: var(--agent-pre-block-bg) !important;
          border-radius: 8px !important;
          padding: 12px !important;
          overflow-x: auto !important;
          margin: 8px 0 !important;
        }

        #trading-agent-container markdown-block pre code {
          background: transparent !important;
          padding: 0 !important;
        }

        #trading-agent-container markdown-block strong {
          color: var(--agent-gold) !important;
          font-weight: 600 !important;
        }

        #trading-agent-container markdown-block ul,
        #trading-agent-container markdown-block ol {
          margin: 8px 0 !important;
          padding-left: 20px !important;
        }

        #trading-agent-container markdown-block li {
          margin: 4px 0 !important;
        }

        #trading-agent-container message-editor {
          background: var(--agent-bg-card) !important;
          padding: 12px 16px 20px 16px !important;
          border-top: 1px solid var(--agent-border-input) !important;
          flex-shrink: 0 !important;
        }

        #trading-agent-container message-editor > div {
          background: var(--agent-bg-input) !important;
          border: 1px solid var(--agent-border-input) !important;
          border-radius: 28px !important;
          padding: 4px 4px 4px 18px !important;
          display: flex !important;
          align-items: center !important;
        }

        #trading-agent-container message-editor > div:focus-within {
          border-color: var(--agent-input-focus-border) !important;
          box-shadow: var(--agent-input-focus-shadow) !important;
        }

        #trading-agent-container message-editor textarea {
          color: var(--agent-text-primary) !important;
          font-size: 14px !important;
          background: transparent !important;
          border: none !important;
          outline: none !important;
          padding: 10px 0 !important;
        }

        #trading-agent-container message-editor textarea::placeholder {
          color: var(--agent-text-extra-dim) !important;
        }

        /* Scrollbar */
        #trading-agent-container *::-webkit-scrollbar {
          width: 4px !important;
        }

        #trading-agent-container *::-webkit-scrollbar-track {
          background: var(--agent-bg-card) !important;
        }

        #trading-agent-container *::-webkit-scrollbar-thumb {
          background: var(--agent-scrollbar-thumb) !important;
          border-radius: 10px !important;
        }

        /* Scrollbar thumb hover */
        #trading-agent-container *::-webkit-scrollbar-thumb:hover {
          background: var(--agent-scrollbar-thumb-hover) !important;
        }

        /* Responsive */
        @media (max-width: 1280px) {
          .agent-sidebar {
            --agent-sidebar-width: 220px !important;
          }
          .agent-panel {
            --agent-panel-width: 250px !important;
          }
        }

        @media (max-width: 1024px) {
          .agent-sidebar {
            --agent-sidebar-width: 0px !important;
            display: none !important;
          }
          .agent-panel {
            --agent-panel-width: 0px !important;
            display: none !important;
          }
        }

        @media (max-width: 768px) {
          .agent-page-layout {
            flex-direction: column;
          }
          .agent-sidebar,
          .agent-panel {
            display: none !important;
          }
          .chat-wrapper {
            padding: 10px;
          }
        }
      `}</style>
    </div>
  );
}

const layoutStyle: React.CSSProperties = {
  width: '100%',
  flex: 1,
  minHeight: 0,
  display: 'flex',
  overflow: 'hidden',
  background: 'var(--agent-bg-deep)',
};
