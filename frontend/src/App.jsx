// App — top-level shell and router (Contracts §6.4 page/route table).
import { Routes, Route } from "react-router-dom";
import { FacilityProvider } from "./context/FacilityContext.jsx";
import ApiKeyGate from "./components/ApiKeyGate.jsx";
import NavBar from "./components/NavBar.jsx";
import ManualConfirmModal from "./components/ManualConfirmModal.jsx";

import Dashboard from "./pages/Dashboard.jsx";
import Discovery from "./pages/Discovery.jsx";
import DeviceList from "./pages/DeviceList.jsx";
import DeviceDetail from "./pages/DeviceDetail.jsx";
import TestExecution from "./pages/TestExecution.jsx";
import TestDetail from "./pages/TestDetail.jsx";
import PunchList from "./pages/PunchList.jsx";
import Checklist from "./pages/Checklist.jsx";
import Attestation from "./pages/Attestation.jsx";
import Reports from "./pages/Reports.jsx";
import Import from "./pages/Import.jsx";
import NotFound from "./pages/NotFound.jsx";

export default function App() {
  return (
    <FacilityProvider>
      <ApiKeyGate>
        <div className="app-shell">
          <NavBar />
          <main className="app-main">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/discovery" element={<Discovery />} />
              <Route path="/devices" element={<DeviceList />} />
              <Route path="/devices/:id" element={<DeviceDetail />} />
              <Route path="/tests" element={<TestExecution />} />
              <Route path="/tests/:id" element={<TestDetail />} />
              <Route path="/punchlist" element={<PunchList />} />
              <Route path="/checklist" element={<Checklist />} />
              <Route path="/attestation" element={<Attestation />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/import" element={<Import />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </main>
          {/* Global modal for manual_confirmation_needed events (§6.2). */}
          <ManualConfirmModal />
        </div>
      </ApiKeyGate>
    </FacilityProvider>
  );
}
