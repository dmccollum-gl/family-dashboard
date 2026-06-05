import { Navigate } from "react-router-dom";

// Admin sections have been merged into the Settings page (owner-only tabs).
export default function Admin() {
  return <Navigate to="/settings" replace />;
}
