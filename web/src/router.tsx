import { createBrowserRouter, type LoaderFunctionArgs, redirect } from 'react-router';
import {
  ApiError,
  getConversation,
  loadAppData,
  NotInvitedError,
  UnauthorizedError,
} from './api/api';
import type { AppData, Conversation } from './api/schemas';
import { AppErrorBoundary, AppLayout } from './components/AppLayout';
import { ChatPage } from './pages/ChatPage';
import { DeniedPage } from './pages/DeniedPage';
import { LoginPage } from './pages/LoginPage';

async function appLoader(): Promise<AppData | Response> {
  try {
    return await loadAppData();
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      return redirect('/login');
    }
    if (error instanceof NotInvitedError) {
      return redirect('/denied');
    }
    throw error;
  }
}

async function conversationLoader({
  params,
}: LoaderFunctionArgs): Promise<Conversation | null | Response> {
  try {
    return await getConversation(params.id ?? '');
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      return redirect('/login');
    }
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export const router = createBrowserRouter([
  { path: '/login', Component: LoginPage },
  { path: '/denied', Component: DeniedPage },
  {
    path: '/',
    id: 'app',
    Component: AppLayout,
    ErrorBoundary: AppErrorBoundary,
    loader: appLoader,
    children: [
      { index: true, Component: ChatPage },
      { path: 'c/:id', Component: ChatPage, loader: conversationLoader },
    ],
  },
]);
