import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Me } from "../types";
import Brand from "./Brand";
import ThemeSwitch from "./ThemeSwitch";

// A component is a function that returns markup (JSX). `children` is whatever is nested inside
// <Layout>…</Layout>, which lets one frame (header + nav) wrap every page. NavLink adds the "active" class
// to the link of the page you are on.
export default function Layout({ me, children }: { me: Me; children: ReactNode }) {
  const qc = useQueryClient();
  const admin = me.role === "admin";
  async function logout() {
    await api.post("/api/auth/logout");
    qc.clear(); // drop all cached data, then "me" refetches and returns 401 -> login page
    window.location.assign("/");
  }
  const items: [string, string, boolean][] = [
    ["/", "Runs", true], ["/analytics", "Analytics", true], ["/playground", "Playground", true], ["/prompts", "Prompts", admin],
    ["/models", "Models", admin], ["/integrations", "Integrations", admin], ["/settings", "Settings", admin], ["/users", "Users", admin], ["/developers", "API", admin], ["/manual", "Manual", true],
  ];
  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand"><Brand />Fedlab Grader</Link>
        <nav>{items.filter(([, , show]) => show).map(([to, label]) => <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>)}</nav>
        <span className="spacer" />
        <span className="who">{me.name || me.login || me.email} · {me.role}</span>
        <ThemeSwitch />
        <button className="link small" onClick={logout}>Log out</button>
      </header>
      <main className="page">{children}</main>
    </>
  );
}
