import { useCallback, useEffect, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import {
  Button,
  Content,
  Header,
  HeaderGlobalAction,
  HeaderGlobalBar,
  HeaderMenuItem,
  HeaderName,
  HeaderNavigation,
  Modal,
  ProgressBar,
  Theme,
} from "@carbon/react";
import { Asleep, Light, Play } from "@carbon/icons-react";
import { getSnapshot, startRun, type Snapshot } from "./api";
import { Dashboard } from "./pages/Dashboard";
import { GraphPage } from "./pages/GraphPage";
import { ImpactPage } from "./pages/ImpactPage";

type Progress = { phase: string; percent: number; message: string };
type ThemeName = "g10" | "g100";

function storedTheme(): ThemeName {
  const saved = localStorage.getItem("dep-intel-theme");
  if (saved === "g100" || saved === "g10") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "g100" : "g10";
}

export function App() {
  const location = useLocation();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [progress, setProgress] = useState<Progress>({ phase: "idle", percent: 0, message: "Waiting to start" });
  const [theme, setTheme] = useState<ThemeName>(storedTheme);
  const [liveTick, setLiveTick] = useState(0);
  const [resync, setResync] = useState("");
  const dark = theme === "g100";

  useEffect(() => {
    document.documentElement.classList.toggle("cds--g100", dark);
    document.documentElement.classList.toggle("cds--g10", !dark);
    localStorage.setItem("dep-intel-theme", theme);
  }, [dark, theme]);

  const refresh = useCallback(async () => {
    try {
      setSnapshot(await getSnapshot());
      setError("");
    } catch {
      setError("Nami Trace is not running. Start it with scripts/nami-trace.sh.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.addEventListener("progress", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as Progress;
      setProgress(data);
      setOpen(true);
      if (data.phase === "complete") void refresh();
    });
    source.addEventListener("file_updated", () => {
      void refresh();
    });
    const bump = () => {
      setLiveTick((value) => value + 1);
      void refresh();
    };
    source.addEventListener("graph_updated", bump);
    source.addEventListener("impact_computed", bump);
    source.addEventListener("job_completed", bump);
    source.addEventListener("job_failed", bump);
    source.addEventListener("resync", () => {
      setResync("The live event stream missed updates. The snapshot was reloaded.");
      bump();
    });
    source.onerror = () => setResync("The live event stream disconnected. Reconnect to load a fresh snapshot.");
    return () => source.close();
  }, [refresh]);

  async function run(path: string) {
    setProgress({ phase: "starting", percent: 0, message: "Starting" });
    setOpen(true);
    await startRun(path);
  }

  return (
    <Theme theme={theme}>
      <Header aria-label="Nami Trace">
        <HeaderName href="/" prefix="">
          Nami Trace
        </HeaderName>
        <HeaderNavigation aria-label="sections">
          <HeaderMenuItem href="/" isCurrentPage={location.pathname === "/"}>
            Dashboard
          </HeaderMenuItem>
          <HeaderMenuItem href="/graph" isCurrentPage={location.pathname === "/graph"}>
            Code graph
          </HeaderMenuItem>
          <HeaderMenuItem href="/impact" isCurrentPage={location.pathname === "/impact"}>
            Impact
          </HeaderMenuItem>
        </HeaderNavigation>
        <HeaderGlobalBar>
          <Button kind="primary" size="md" renderIcon={Play} onClick={() => void run("/api/analysis/run")}>
            Run analysis
          </Button>
          <HeaderGlobalAction
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            tooltipAlignment="end"
            onClick={() => setTheme(dark ? "g10" : "g100")}
          >
            {dark ? <Light size={20} /> : <Asleep size={20} />}
          </HeaderGlobalAction>
        </HeaderGlobalBar>
      </Header>
      <Content className="app-content">
        <Routes>
          <Route path="/" element={<Dashboard snapshot={snapshot} error={error} loaded={loaded} />} />
          <Route
            path="/graph"
            element={<GraphPage snapshot={snapshot} error={error} loaded={loaded} onBuild={() => void run("/api/graphs/build")} />}
          />
          <Route path="/impact" element={<ImpactPage tick={liveTick} resync={resync} />} />
        </Routes>
      </Content>
      <Modal
        open={open}
        passiveModal
        modalHeading="Analysis"
        onRequestClose={() => setOpen(false)}
      >
        <p>{progress.message}</p>
        <ProgressBar label="Analysis progress" value={progress.percent} max={100} status={progress.percent >= 100 ? "finished" : "active"} />
      </Modal>
    </Theme>
  );
}
