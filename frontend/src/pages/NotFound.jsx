import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="page">
      <div className="state state-empty">
        <div className="state-title">404 — Not found</div>
        <div className="state-detail">That route does not exist.</div>
        <Link to="/" className="btn btn-primary">
          Back to overview
        </Link>
      </div>
    </div>
  );
}
