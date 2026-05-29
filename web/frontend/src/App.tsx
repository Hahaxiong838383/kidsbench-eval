import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import AdapterDetail from "./pages/AdapterDetail";
import AdaptersIndex from "./pages/AdaptersIndex";
import MemoryDetail from "./pages/MemoryDetail";
import MemoryIndex from "./pages/MemoryIndex";
import GroupDetail from "./pages/GroupDetail";
import LLMPresets from "./pages/LLMPresets";
import Runs from "./pages/Runs";
import System from "./pages/System";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="adapters" element={<AdaptersIndex />} />
        <Route path="adapters/:name" element={<AdapterDetail />} />
        <Route path="memory" element={<MemoryIndex />} />
        <Route path="memory/:name" element={<MemoryDetail />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:group" element={<GroupDetail />} />
        <Route path="llm" element={<LLMPresets />} />
        <Route path="system" element={<System />} />
      </Route>
    </Routes>
  );
}
