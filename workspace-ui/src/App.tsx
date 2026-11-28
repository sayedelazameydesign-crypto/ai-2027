import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  Cable,
  ChevronLeft,
  FolderGit2,
  Languages,
  Library,
  ListChecks,
  Menu,
  Settings as SettingsIcon,
  Sparkles,
  TerminalSquare,
  X,
} from 'lucide-react';
import { StudioWorkspace } from './StudioWorkspace';
import { ConnectorsWorkspace } from './ConnectorsWorkspace';
import { LibraryWorkspace } from './LibraryWorkspace';
import { ProjectsWorkspace } from './ProjectsWorkspace';
import { SkillsWorkspace } from './SkillsWorkspace';
import { SettingsWorkspace } from './SettingsWorkspace';
import { KernelWorkspace } from './KernelWorkspace';
import type { ConnectionState, Language } from './integrations';

type Section = 'tasks' | 'projects' | 'library' | 'skills' | 'connectors' | 'settings';
type TasksTab = 'studio' | 'kernel';

const copyByLanguage = {
  ar: {
    productTag: 'تنفيذ موثّق · دليل قبل ادعاء',
    assistant: 'Celia',
    workspace: 'مساحة العمل',
    tasks: 'المهام',
    projects: 'المشاريع',
    library: 'المكتبة',
    skills: 'المهارات',
    connectors: 'الموصلات',
    settings: 'الإعدادات والحوكمة',
    console: 'لوحة التشغيل',
    newTask: 'مهمة جديدة',
    recentRuns: 'عمليات حديثة',
    operations: 'مركز العمليات',
    connected: 'متصل',
    offline: 'غير متصل',
    checking: 'جارٍ التحقق…',
    checkingConnection: 'جارٍ التحقق من الاتصال…',
    studioConnected: 'Agent Studio متصل',
    studioOffline: 'Agent Studio غير متاح',
    hubConnected: 'Connectors Hub متصل',
    hubOffline: 'Connectors Hub غير متاح',
    kernelConnected: 'النواة متصلة',
    kernelOffline: 'النواة غير متصلة',
    libraryConnected: 'المكتبة متصلة',
    libraryOffline: 'المكتبة غير متاحة',
    switchLanguage: 'English',
    tasksNav: 'المهام · Agent Studio',
    tabStudio: 'Agent Studio',
    tabKernel: 'التنفيذ المحلي · Live Agent',
    footer: 'CELIA.SH 2027 · الدليل قبل الادعاء',
  },
  en: {
    productTag: 'Verified execution · evidence before claims',
    assistant: 'Celia',
    workspace: 'Workspace',
    tasks: 'Tasks',
    projects: 'Projects',
    library: 'Library',
    skills: 'Skills',
    connectors: 'Connectors',
    settings: 'Settings & Governance',
    console: 'Operations console',
    newTask: 'New task',
    recentRuns: 'Recent runs',
    operations: 'Operations center',
    connected: 'Connected',
    offline: 'Offline',
    checking: 'Checking…',
    checkingConnection: 'Checking connection…',
    studioConnected: 'Agent Studio connected',
    studioOffline: 'Agent Studio unavailable',
    hubConnected: 'Connectors Hub connected',
    hubOffline: 'Connectors Hub unavailable',
    kernelConnected: 'Kernel connected',
    kernelOffline: 'Kernel offline',
    libraryConnected: 'Library connected',
    libraryOffline: 'Library unavailable',
    switchLanguage: 'العربية',
    tasksNav: 'Tasks · Agent Studio',
    tabStudio: 'Agent Studio',
    tabKernel: 'Local execution · Live Agent',
    footer: 'CELIA.SH 2027 · Evidence before claims',
  },
} as const;

type Copy = { [Key in keyof (typeof copyByLanguage)['en']]: string };

function languageFromStorage(): Language {
  try {
    return localStorage.getItem('ai2027-workspace-language') === 'en' ? 'en' : 'ar';
  } catch {
    return 'ar';
  }
}

/** Native, zero-external-link CELIA.SH mark. */
function CeliaMark({ size = 34 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      role="img"
      aria-label="CELIA.SH"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient id="celiaMark" x1="6" y1="6" x2="34" y2="34" gradientUnits="userSpaceOnUse">
          <stop stopColor="#c6f9cf" />
          <stop offset="1" stopColor="#7be3a2" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="36" height="36" rx="11" fill="url(#celiaMark)" />
      <path
        d="M27 14.5a10 10 0 1 0 2.4 7.2"
        stroke="#0e1410"
        strokeWidth="3.4"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx="29.4" cy="21.7" r="2.6" fill="#0e1410" />
    </svg>
  );
}

interface TasksWorkspaceProps {
  lang: Language;
  resetKey: number;
  initialSessionId: string | null;
  onConnectionChange: (state: ConnectionState) => void;
}

function TasksWorkspace({ lang, resetKey, initialSessionId, onConnectionChange }: TasksWorkspaceProps) {
  const [tab, setTab] = useState<TasksTab>('studio');
  const t = copyByLanguage[lang];
  return (
    <div className="tasks-workspace" dir={lang === 'ar' ? 'rtl' : 'ltr'}>
      <div className="tasks-tabs" role="tablist" aria-label={t.tasksNav}>
        <button
          role="tab"
          aria-selected={tab === 'studio'}
          className={`tasks-tab ${tab === 'studio' ? 'active' : ''}`}
          onClick={() => setTab('studio')}
        >
          <Sparkles size={15} />{t.tabStudio}
        </button>
        <button
          role="tab"
          aria-selected={tab === 'kernel'}
          className={`tasks-tab ${tab === 'kernel' ? 'active' : ''}`}
          onClick={() => setTab('kernel')}
        >
          <TerminalSquare size={15} />{t.tabKernel}
        </button>
      </div>
      {tab === 'studio' ? (
        <StudioWorkspace
          lang={lang}
          resetKey={resetKey}
          initialSessionId={initialSessionId}
          onConnectionChange={onConnectionChange}
        />
      ) : (
        <KernelWorkspace lang={lang} onConnectionChange={onConnectionChange} />
      )}
    </div>
  );
}

export default function App() {
  const [lang, setLang] = useState<Language>(languageFromStorage);
  const t = copyByLanguage[lang];
  const direction = lang === 'ar' ? 'rtl' : 'ltr';
  const [activeSection, setActiveSection] = useState<Section>('tasks');
  const [pageConnection, setPageConnection] = useState<ConnectionState>('checking');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [tasksResetKey, setTasksResetKey] = useState(0);
  const [initialSessionId, setInitialSessionId] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = direction;
    document.title = lang === 'ar' ? 'CELIA.SH 2027 · مساحة العمل' : 'CELIA.SH 2027 · Workspace';
    try {
      localStorage.setItem('ai2027-workspace-language', lang);
    } catch {
      // The app still works when browser storage is disabled.
    }
  }, [lang, direction]);

  // Static sections (honesty map, skills) are served from the local build.
  useEffect(() => {
    if (activeSection === 'projects' || activeSection === 'skills') {
      setPageConnection('online');
    }
  }, [activeSection]);

  const startNewTask = useCallback(() => {
    setInitialSessionId(null);
    setActiveSection('tasks');
    setTasksResetKey((current) => current + 1);
    setMobileMenuOpen(false);
  }, []);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        startNewTask();
      }
    };
    window.addEventListener('keydown', onShortcut);
    return () => window.removeEventListener('keydown', onShortcut);
  }, [startNewTask]);

  const openSessionFromLibrary = useCallback((sessionId: string) => {
    setInitialSessionId(sessionId);
    setActiveSection('tasks');
    setTasksResetKey((current) => current + 1);
    setMobileMenuOpen(false);
  }, []);

  const connectionLabel = (): string => {
    if (pageConnection === 'checking') return t.checkingConnection;
    const online = pageConnection === 'online';
    switch (activeSection) {
      case 'tasks': return online ? t.studioConnected : t.studioOffline;
      case 'connectors': return online ? t.hubConnected : t.hubOffline;
      case 'library': return online ? t.libraryConnected : t.libraryOffline;
      case 'projects':
      case 'skills':
      case 'settings': return online ? t.connected : t.offline;
      default: return online ? t.connected : t.offline;
    }
  };

  const navItems: Array<{ id: Section; label: string; icon: typeof Cable; subtitle?: string }> = [
    { id: 'tasks', label: t.tasks, icon: ListChecks },
    { id: 'projects', label: t.projects, icon: FolderGit2 },
    { id: 'library', label: t.library, icon: Library },
    { id: 'skills', label: t.skills, icon: Sparkles },
    { id: 'connectors', label: t.connectors, icon: Cable },
    { id: 'settings', label: t.settings, icon: SettingsIcon },
  ];

  return (
    <div className="app-frame" dir={direction}>
      {mobileMenuOpen && (
        <button className="mobile-scrim" aria-label="Close menu" onClick={() => setMobileMenuOpen(false)} />
      )}
      <aside className={`sidebar ${mobileMenuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-row">
          <div className="brand-mark"><CeliaMark size={34} /></div>
          <div className="brand-copy">
            <span className="brand-name">CELIA<span>.SH</span></span>
            <span className="brand-subtitle">{t.productTag}</span>
          </div>
          <button className="icon-button mobile-close" aria-label="Close menu" onClick={() => setMobileMenuOpen(false)}><X size={17} /></button>
        </div>

        <button className="new-task-button" onClick={startNewTask}>
          <Sparkles size={17} />
          <span>{t.newTask}</span>
          <span className="shortcut">⌘ K</span>
        </button>

        <div className="side-label">{t.workspace}</div>
        <nav className="side-nav" aria-label={t.workspace}>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={`nav-item ${activeSection === item.id ? 'active' : ''}`}
                onClick={() => { setActiveSection(item.id); setMobileMenuOpen(false); if (item.id === 'tasks') setTasksResetKey((c) => c + 1); }}
                aria-current={activeSection === item.id ? 'page' : undefined}
              >
                <Icon size={16} />
                <span>{item.label}</span>
                {activeSection === item.id && <span className="nav-indicator" />}
              </button>
            );
          })}
        </nav>

        <a className="nav-item nav-link" href="/ops/" onClick={() => setMobileMenuOpen(false)}>
          <TerminalSquare size={16} />
          <span>{t.console}</span>
          <ChevronLeft size={14} className="nav-external" />
        </a>

        <div className="sidebar-bottom">
          <div className="sidebar-runtime">
            <span className={`runtime-indicator ${pageConnection === 'online' ? 'online' : pageConnection === 'checking' ? 'checking' : 'offline'}`} />
            <div>
              <strong>{connectionLabel()}</strong>
              <span>{t.assistant} · Agent Studio · API</span>
            </div>
          </div>
          <div className="sidebar-footer"><Sparkles size={13} />{t.footer}</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu-button" aria-label="Open menu" onClick={() => setMobileMenuOpen(true)}><Menu size={19} /></button>
          <div className="breadcrumbs"><span>{t.workspace}</span><ChevronLeft size={13} /><strong>{navItems.find((n) => n.id === activeSection)?.label ?? ''}</strong></div>
          <div className="topbar-actions">
            <div className={`connection-pill ${pageConnection === 'online' ? 'is-online' : pageConnection === 'checking' ? 'is-checking' : 'is-offline'}`}>
              <span className="connection-dot" />
              <span>{connectionLabel()}</span>
            </div>
            <button className="language-button" onClick={() => setLang((c) => (c === 'ar' ? 'en' : 'ar'))} aria-label={t.switchLanguage}>
              <Languages size={15} />
              <span>{t.switchLanguage}</span>
            </button>
            <div className="user-avatar" aria-label="Celia">C</div>
          </div>
        </header>

        <div className="page-view-shell">
          <div hidden={activeSection !== 'tasks'}>
            <TasksWorkspace
              lang={lang}
              resetKey={tasksResetKey}
              initialSessionId={initialSessionId}
              onConnectionChange={setPageConnection}
            />
          </div>
          <div hidden={activeSection !== 'projects'}>
            <ProjectsWorkspace lang={lang} onConnectionChange={setPageConnection} onOpenSection={() => setActiveSection('projects')} />
          </div>
          <div hidden={activeSection !== 'library'}>
            <LibraryWorkspace lang={lang} onConnectionChange={setPageConnection} onOpenSession={openSessionFromLibrary} />
          </div>
          <div hidden={activeSection !== 'skills'}>
            <SkillsWorkspace lang={lang} onConnectionChange={setPageConnection} />
          </div>
          <div hidden={activeSection !== 'connectors'}>
            <ConnectorsWorkspace lang={lang} onConnectionChange={setPageConnection} />
          </div>
          <div hidden={activeSection !== 'settings'}>
            <SettingsWorkspace lang={lang} onConnectionChange={setPageConnection} onOpenProjects={() => setActiveSection('projects')} />
          </div>
        </div>
      </main>
    </div>
  );
}
