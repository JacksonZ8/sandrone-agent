import { useState } from "react";
import AppShell from "./app/AppShell";
import { useRoute } from "./app/route";
import { statusText, useAppState } from "./app/useAppState";
import ChatScreen from "./chat/ChatScreen";
import SettingsPanel from "./components/SettingsPanel";
import GamesScreen from "./screens/GamesScreen";
import UsagePanel from "./components/UsagePanel";
import HerScreen from "./screens/HerScreen";
import MemoryScreen from "./screens/MemoryScreen";
import WallpaperScreen from "./screens/WallpaperScreen";
import { shouldShowLaunch } from "./stage/launch";
import LaunchSplash from "./stage/LaunchSplash";

export default function App() {
  const [route, navigate] = useRoute();
  const { state, error, refresh } = useAppState();
  const [launching, setLaunching] = useState(shouldShowLaunch);
  const status = statusText(state, error);
  const refreshState = () => void refresh();

  return (
    <>
      <AppShell route={route} navigate={navigate} status={status}>
        {route === "chat" && <ChatScreen appState={state} onStateChange={refreshState} />}
        {route === "her" && <HerScreen appState={state} navigate={navigate} />}
        {route === "memory" && <MemoryScreen onMemoryChange={refreshState} />}
        {route === "games" && <GamesScreen navigate={navigate} />}
        {route === "wallpaper" && <WallpaperScreen />}
        {route === "settings" && <SettingsPanel onConfigSaved={refreshState} />}
        {route === "usage" && <UsagePanel />}
      </AppShell>
      {launching && <LaunchSplash onDone={() => setLaunching(false)} />}
    </>
  );
}
