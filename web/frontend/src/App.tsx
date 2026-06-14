import { Route, Routes } from "react-router-dom";
import { SourceProvider } from "./lib/sourceContext";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import AdapterDetail from "./pages/AdapterDetail";
import AdaptersIndex from "./pages/AdaptersIndex";
import MemoryDetail from "./pages/MemoryDetail";
import MemoryIndex from "./pages/MemoryIndex";
import GroupDetail from "./pages/GroupDetail";
import LiveRun from "./pages/LiveRun";
import LLMPresets from "./pages/LLMPresets";
import QuestionBank from "./pages/QuestionBank";
import Banks from "./pages/Banks";
import Runs from "./pages/Runs";
import System from "./pages/System";
import Admin from "./pages/Admin";

export default function App() {
  return (
    <SourceProvider>
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="adapters" element={<AdaptersIndex />} />
        <Route path="adapters/:name" element={<AdapterDetail />} />
        <Route path="memory" element={<MemoryIndex />} />
        <Route path="memory/:name" element={<MemoryDetail />} />
        <Route path="questionbank" element={<QuestionBank />} />
        <Route path="banks" element={<Banks />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:group" element={<GroupDetail />} />
        <Route path="live" element={<LiveRun />} />
        <Route path="llm" element={<LLMPresets />} />
        <Route path="system" element={<System />} />
        <Route path="admin" element={<Admin />} />
      </Route>
    </Routes>
    </SourceProvider>
  );
}
