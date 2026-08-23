import { create } from "zustand";

export interface User {
  user_id: number;
  username: string;
  role: "admin" | "user";
}

interface AuthState {
  token: string | null;
  user: User | null;
  login: (token: string, user: User) => void;
  logout: () => void;
}

const TOKEN_KEY = "ai-study-token";
const USER_KEY = "ai-study-user";

function readToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

function readUser(): User | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  token: readToken(),
  user: readUser(),
  login: (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    set({ token, user });
  },
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    set({ token: null, user: null });
  },
}));

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
