"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";

export type Role = "owner" | "admin" | "agent" | "viewer";

export interface Membership {
  org_id: string;
  org_name: string;
  role: Role;
}

export interface SessionUser {
  id: string;
  email: string;
  name: string;
  memberships: Membership[];
}

export function login(email: string, password: string): Promise<SessionUser> {
  return api<SessionUser>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function signup(input: {
  email: string;
  password: string;
  name: string;
  org_name: string;
}): Promise<SessionUser> {
  return api<SessionUser>("/auth/signup", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function logout(): Promise<void> {
  return api<void>("/auth/logout", { method: "POST" });
}

export function fetchMe(): Promise<SessionUser> {
  return api<SessionUser>("/auth/me");
}

async function resolveSession(): Promise<SessionUser | null> {
  try {
    return await fetchMe();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      // Access token may have expired; try a refresh once before giving up.
      try {
        await api("/auth/refresh", { method: "POST" });
        return await fetchMe();
      } catch {
        return null;
      }
    }
    return null;
  }
}

export function useSession() {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const resolved = await resolveSession();
    setUser(resolved);
    setLoading(false);
    return resolved;
  }, []);

  useEffect(() => {
    let active = true;
    resolveSession().then((resolved) => {
      if (active) {
        setUser(resolved);
        setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  return { user, loading, refresh };
}
