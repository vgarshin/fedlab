import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import Switch from "../components/Switch";
import type { Role, UserRow } from "../types";

export default function UsersPage() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<UserRow[]>("/api/users") });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const add = useMutation({ mutationFn: () => api.post("/api/users", { email, role }), onSuccess: () => { setEmail(""); refresh(); } });
  const patch = useMutation({ mutationFn: (v: { id: number; role?: Role; active?: boolean }) => api.patch(`/api/users/${v.id}`, v), onSuccess: refresh });

  return (
    <>
      <h1>Users</h1>
      <p className="muted">People sign in with Yandex ID; only e-mails listed here get access.</p>
      <form className="card row" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input type="email" placeholder="Yandex e-mail" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}><option value="viewer">viewer</option><option value="admin">admin</option></select>
        <button className="btn primary" disabled={add.isPending}>Add user</button>
        {add.isError && <span className="error">{String(add.error)}</span>}
      </form>
      <div className="tscroll"><table>
        <thead><tr><th>E-mail</th><th>Name</th><th>Role</th><th>Active</th><th>Last login</th></tr></thead>
        <tbody>
          {users.data?.map((u) => (
            <tr key={u.id}>
              <td>{u.email}</td><td>{u.name || u.login}</td>
              <td><select value={u.role} onChange={(e) => patch.mutate({ id: u.id, role: e.target.value as Role })}><option>viewer</option><option>admin</option></select></td>
              <td><Switch checked={u.active} onChange={(on) => patch.mutate({ id: u.id, active: on })} /></td>
              <td className="muted">{u.last_login ? new Date(u.last_login).toLocaleString() : "never"}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {patch.isError && <p className="error">{String(patch.error)}</p>}
    </>
  );
}
