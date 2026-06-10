import axios from "axios";

const api = axios.create({ withCredentials: true });

// If the server says we're not signed in (session expired or never existed),
// drop any stale client-side identity so the UI reflects a signed-out state.
// The backend is the source of truth — localStorage is only a cache.
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem("dashboard_active_user");
      localStorage.removeItem("dashboard_role");
    }
    return Promise.reject(err);
  }
);

export default api;
