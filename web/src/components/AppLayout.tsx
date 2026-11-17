import { Alert, Box, Drawer } from '@mui/material';
import { Outlet, useRouteLoaderData } from 'react-router';
import type { AppData } from '../api/schemas';
import { Sidebar } from './Sidebar';

const drawerWidth = 280;

export function AppErrorBoundary() {
  return (
    <Box sx={{ p: 3 }}>
      <Alert severity="error">Something went wrong. Please reload the page and try again.</Alert>
    </Box>
  );
}

export function AppLayout() {
  const data = useRouteLoaderData<AppData>('app');

  if (!data) {
    return null;
  }

  return (
    <Box sx={{ display: 'flex' }}>
      <Drawer
        variant="permanent"
        sx={{
          width: drawerWidth,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            width: drawerWidth,
            boxSizing: 'border-box',
            bgcolor: 'background.paper',
            borderRight: 1,
            borderColor: 'divider',
          },
        }}
      >
        <Sidebar me={data.me} conversations={data.conversations} />
      </Drawer>
      <Box
        component="main"
        sx={{ flexGrow: 1, minWidth: 0, minHeight: '100vh', bgcolor: 'background.default' }}
      >
        <Outlet />
      </Box>
    </Box>
  );
}
