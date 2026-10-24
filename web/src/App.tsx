import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import {
  Content,
  Header,
  HeaderGlobalAction,
  HeaderGlobalBar,
  HeaderMenu,
  HeaderMenuButton,
  HeaderMenuItem,
  HeaderName,
  HeaderNavigation,
  Button,
  HeaderPanel,
  SideNav,
  SideNavItems,
  SideNavLink,
  SideNavMenu,
  SideNavMenuItem,
  InlineLoading,
  ProgressBar,
  Search,
  SkipToContent,
  Theme,
} from "@carbon/react";
import { Activity, Asleep, Light } from "@carbon/icons-react";
import { getAppState, refreshIntelligence, searchWorkspace, type AppState, type SearchHit } from "./api";
import { StatusTag } from "./components/StatusTag";
import { intelligenceView } from "./intelligence";
import { ArchitecturePage } from "./pages/ArchitecturePage";
import { ImpactExplorerPage } from "./pages/ImpactExplorerPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RepositoryHealthPage } from "./pages/RepositoryHealthPage";
import { ResearchPage } from "./pages/ResearchPage";
import { ReviewDetailPage } from "./pages/ReviewDetailPage";
import { ReviewPage } from "./pages/ReviewPage";

type Progress = { phase: string; percent: number; message: string; operation?: string };
type ThemeName = "g10" | "g100";

function storedTheme(): ThemeName {
  const saved = localStorage.getItem("dep-intel-theme");
  if (saved === "g100" || saved === "g10") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "g100" : "g10";
}

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState<AppState | null>(null);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [progress, setProgress] = useState<Progress>({ phase: "idle", percent: 0, message: "" });
  const [theme, setTheme] = useState<ThemeName>(storedTheme);
  const [panel, setPanel] = useState(false);
  const [menu, setMenu] = useState(false);
  const [query, setQuery] = useState("");
  const [streamNote, setStreamNote] = useState("");
  const dark = theme === "g100";
  const refreshing = snapshot?.running || (progress.phase !== "idle" && progress.phase !== "complete");
  const view = intelligenceView(snapshot, Boolean(refreshing));

  useEffect(() => {
    document.documentElement.classList.toggle("cds--g100", dark);
    document.documentElement.classList.toggle("cds--g10", !dark);
    localStorage.setItem("dep-intel-theme", theme);
  }, [dark, theme]);

  useEffect(() => {
    setMenu(false);
  }, [location.pathname]);

  const refreshTimer = useRef<number | null>(null);
  const refresh = useCallback(async () => {
    try {
      setSnapshot(await getAppState());
      setError("");
    } catch {
      setError("Nami Trace is not running. Start it with scripts/nami-trace.sh.");
    } finally {
      setLoaded(true);
    }
  }, []);
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(() => void refresh(), 400);
  }, [refresh]);

  useEffect(() => {
    void refresh();
    return () => {
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
    };
  }, [refresh]);

  useEffect(() => {
    const source = new EventSource("/api/events");
    let opened = false;
    source.addEventListener("hello", () => {
      opened = true;
    });
    source.addEventListener("progress", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as Progress;
      if (data.operation !== "intelligence_refresh") return;
      setProgress(data);
      if (data.phase === "complete") scheduleRefresh();
    });
    source.addEventListener("file_updated", () => scheduleRefresh());
    source.addEventListener("graph_updated", () => scheduleRefresh());
    source.addEventListener("job_completed", () => scheduleRefresh());
    source.addEventListener("job_failed", () => scheduleRefresh());
    source.addEventListener("resync", () => {
      setStreamNote("The live event stream missed updates. The application state was reloaded.");
      scheduleRefresh();
    });
    source.onerror = () => {
      if (opened) setStreamNote("The live event stream disconnected. Reload to fetch fresh application state.");
    };
    return () => source.close();
  }, [scheduleRefresh]);

  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) {
      setHits([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void searchWorkspace(term, 8, controller.signal).then(setHits).catch(() => undefined);
    }, 250);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query]);

  async function refreshCore() {
    setProgress({ phase: "repository_graph", percent: 5, message: "Refreshing intelligence" });
    const started = await refreshIntelligence();
    if (!started.started) setProgress({ phase: "complete", percent: 100, message: started.message || "A refresh is already running." });
  }

  const current = (path: string) => location.pathname === path || location.pathname.startsWith(`${path}/`);
  const follow = (event: React.MouseEvent) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    const href = (event.currentTarget as HTMLAnchorElement).getAttribute("href");
    if (!href || href.startsWith("http")) return;
    event.preventDefault();
    navigate(href);
  };

  return (
    <Theme theme={theme}>
      <Header aria-label="Nami Trace">
        <SkipToContent href="#main-content" />
        <HeaderMenuButton
          aria-label={menu ? "Close menu" : "Open menu"}
          isActive={menu}
          isCollapsible
          onClick={() => setMenu((open) => !open)}
        />
        <HeaderName href="/" prefix="" onClick={follow}>
          Nami Trace
        </HeaderName>
        <HeaderNavigation aria-label="Primary">
          <HeaderMenuItem href="/" isCurrentPage={location.pathname === "/"} onClick={follow}>Overview</HeaderMenuItem>
          <HeaderMenuItem href="/review" isCurrentPage={current("/review")} onClick={follow}>Review change</HeaderMenuItem>
          <HeaderMenuItem href="/impact" isCurrentPage={current("/impact")} onClick={follow}>Impact explorer</HeaderMenuItem>
          <HeaderMenuItem href="/architecture" isCurrentPage={current("/architecture")} onClick={follow}>Architecture</HeaderMenuItem>
          <HeaderMenu aria-label="More" menuLinkName="More">
            <HeaderMenuItem href="/health" isCurrentPage={current("/health")} onClick={follow}>Repository health</HeaderMenuItem>
            <HeaderMenuItem href="/research" isCurrentPage={current("/research")} onClick={follow}>Research</HeaderMenuItem>
          </HeaderMenu>
        </HeaderNavigation>
        <SideNav aria-label="Primary" expanded={menu} isPersistent={false} onOverlayClick={() => setMenu(false)}>
          <SideNavItems>
            <SideNavLink href="/" isActive={location.pathname === "/"} onClick={follow}>Overview</SideNavLink>
            <SideNavLink href="/review" isActive={current("/review")} onClick={follow}>Review change</SideNavLink>
            <SideNavLink href="/impact" isActive={current("/impact")} onClick={follow}>Impact explorer</SideNavLink>
            <SideNavLink href="/architecture" isActive={current("/architecture")} onClick={follow}>Architecture</SideNavLink>
            <SideNavMenu title="More" defaultExpanded>
              <SideNavMenuItem href="/health" isActive={current("/health")} onClick={follow}>Repository health</SideNavMenuItem>
              <SideNavMenuItem href="/research" isActive={current("/research")} onClick={follow}>Research</SideNavMenuItem>
            </SideNavMenu>
          </SideNavItems>
        </SideNav>
        <HeaderGlobalBar>
          <form
            className="header-search"
            role="search"
            aria-label="Search"
            onSubmit={(event) => {
              event.preventDefault();
              navigate(`/architecture?q=${encodeURIComponent(query)}`);
            }}
          >
            <Search
              size="sm"
              labelText="Search symbols, files, classes, functions"
              placeholder="Search"
              closeButtonLabelText="Clear search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            {hits.length ? (
              <ul className="search-results">
                {hits.map((hit) => (
                  <li key={hit.key}>
                    <button
                      type="button"
                      onClick={() => {
                        setHits([]);
                        setQuery("");
                        navigate(`/architecture?service=${encodeURIComponent(hit.service_id)}&target=${encodeURIComponent(hit.qualified_name || hit.key)}`);
                      }}
                    >
                      <strong>{hit.name || hit.qualified_name}</strong>
                      <span>{hit.service_id}{hit.file_path ? ` · ${hit.file_path}` : ""}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </form>
          <HeaderGlobalAction aria-label="Intelligence status" tooltipAlignment="end" isActive={panel} onClick={() => setPanel((open) => !open)}>
            <Activity size={20} />
          </HeaderGlobalAction>
          <HeaderGlobalAction
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            tooltipAlignment="end"
            onClick={() => setTheme(dark ? "g10" : "g100")}
          >
            {dark ? <Light size={20} /> : <Asleep size={20} />}
          </HeaderGlobalAction>
        </HeaderGlobalBar>
        <HeaderPanel aria-label="Intelligence status" expanded={panel}>
          <div className="status-panel">
            <h2>Intelligence status</h2>
            <dl>
              <div><dt>Cluster graph</dt><dd><StatusTag status={view.cluster} /></dd></div>
              <div><dt>Commit history</dt><dd><StatusTag status={view.history} /></dd></div>
              <div><dt>Contracts</dt><dd><StatusTag status={view.contracts} /></dd></div>
              <div><dt>Stale services</dt><dd>{view.stale}</dd></div>
              <div><dt>Unresolved refs</dt><dd>{view.unresolved}</dd></div>
            </dl>
            <p className="helper">Last refreshed</p>
            <p>{view.refreshedAt}</p>
            {refreshing ? <InlineLoading description="Refreshing intelligence" /> : (
              <Button kind="primary" size="sm" onClick={() => void refreshCore()}>Refresh intelligence</Button>
            )}
          </div>
        </HeaderPanel>
      </Header>
      <Content id="main-content" className="app-content" aria-label="Main">
        {streamNote ? <p className="helper">{streamNote}</p> : null}
        {refreshing && progress.message ? (
          <div className="activity-strip" role="status">
            <p>{progress.message}</p>
            <ProgressBar label={progress.message} value={progress.percent || undefined} max={100} status="active" />
            <p className="helper">Displayed results are from the previous snapshot.</p>
          </div>
        ) : null}
        <Routes>
          <Route path="/" element={<OverviewPage snapshot={snapshot} error={error} loaded={loaded} refreshing={Boolean(refreshing)} onRefresh={() => void refreshCore()} />} />
          <Route path="/review" element={<ReviewPage snapshot={snapshot} />} />
          <Route path="/review/:reviewId" element={<ReviewDetailPage />} />
          <Route path="/impact" element={<ImpactExplorerPage snapshot={snapshot} />} />
          <Route path="/architecture" element={<ArchitecturePage snapshot={snapshot} loaded={loaded} error={error} />} />
          <Route path="/graph" element={<Navigate to="/architecture" replace />} />
          <Route path="/health" element={<RepositoryHealthPage loaded={loaded} />} />
          <Route path="/research" element={<ResearchPage snapshot={snapshot} />} />
        </Routes>
      </Content>
    </Theme>
  );
}
