import { NavLink, Route, Routes } from "react-router-dom";
import Library from "./pages/Library";
import Project from "./pages/Project";
import ScriptTOC from "./pages/ScriptTOC";
import Brief from "./pages/Brief";
import Settings from "./pages/Settings";

const navItem =
  "px-3 py-1.5 rounded-md text-sm hover:bg-neutral-800 transition-colors";
const navActive = "bg-neutral-800 text-white";
const navInactive = "text-neutral-300";

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-neutral-800 bg-neutral-950">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
          <span className="font-semibold tracking-tight">ClipFarm</span>
          <nav className="flex gap-1">
            <NavLink
              to="/library"
              className={({ isActive }) =>
                `${navItem} ${isActive ? navActive : navInactive}`
              }
            >
              Library
            </NavLink>
            <NavLink
              to="/project"
              className={({ isActive }) =>
                `${navItem} ${isActive ? navActive : navInactive}`
              }
            >
              Project
            </NavLink>
            <NavLink
              to="/script"
              className={({ isActive }) =>
                `${navItem} ${isActive ? navActive : navInactive}`
              }
            >
              Script
            </NavLink>
            <NavLink
              to="/brief"
              className={({ isActive }) =>
                `${navItem} ${isActive ? navActive : navInactive}`
              }
            >
              Brief
            </NavLink>
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                `${navItem} ${isActive ? navActive : navInactive}`
              }
            >
              Settings
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-6xl mx-auto w-full px-4 py-8">
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/library" element={<Library />} />
          <Route path="/project" element={<Project />} />
          <Route path="/script" element={<ScriptTOC />} />
          <Route path="/brief" element={<Brief />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
