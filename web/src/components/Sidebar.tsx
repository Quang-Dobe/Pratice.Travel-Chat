import AddRounded from '@mui/icons-material/AddRounded';
import ChatBubbleOutlineRounded from '@mui/icons-material/ChatBubbleOutlineRounded';
import DarkModeRounded from '@mui/icons-material/DarkModeRounded';
import FlightTakeoffRounded from '@mui/icons-material/FlightTakeoffRounded';
import LightModeRounded from '@mui/icons-material/LightModeRounded';
import LogoutRounded from '@mui/icons-material/LogoutRounded';
import {
  Avatar,
  Box,
  Button,
  ButtonBase,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Tooltip,
  Typography,
  useColorScheme,
} from '@mui/material';
import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router';
import { type AppData, logout } from '../api/api';
import { brandGradient } from '../theme';
import { initials } from './initials';
import { ProfileDialog } from './ProfileDialog';

function ThemeToggle() {
  const { mode, systemMode, setMode } = useColorScheme();
  const isDark = (mode === 'system' ? systemMode : mode) === 'dark';
  const label = isDark ? 'Switch to light mode' : 'Switch to dark mode';
  return (
    <Tooltip title={label}>
      <IconButton
        aria-label={label}
        size="small"
        onClick={() => setMode(isDark ? 'light' : 'dark')}
      >
        {isDark ? <LightModeRounded fontSize="small" /> : <DarkModeRounded fontSize="small" />}
      </IconButton>
    </Tooltip>
  );
}

export function Sidebar({ me, conversations }: AppData) {
  const navigate = useNavigate();
  const [profileOpen, setProfileOpen] = useState(false);

  async function handleLogout() {
    await logout().catch(() => undefined);
    await navigate('/login');
  }

  return (
    <Box
      component="nav"
      sx={{ display: 'flex', flexDirection: 'column', height: '100%', p: 2, gap: 2 }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, px: 0.5 }}>
        <Box
          sx={{
            width: 36,
            height: 36,
            borderRadius: 2.5,
            display: 'grid',
            placeItems: 'center',
            color: '#fff',
            background: brandGradient,
          }}
        >
          <FlightTakeoffRounded fontSize="small" />
        </Box>
        <Typography variant="h6" component="span" sx={{ flexGrow: 1 }}>
          Travel Chat
        </Typography>
        <ThemeToggle />
      </Box>

      <Button
        component={NavLink}
        to="/"
        variant="contained"
        size="large"
        startIcon={<AddRounded />}
      >
        New conversation
      </Button>

      <Typography variant="overline" color="text.secondary" sx={{ px: 1, lineHeight: 1 }}>
        Recent trips
      </Typography>
      <List
        aria-label="Conversation history"
        dense
        disablePadding
        sx={{
          flexGrow: 1,
          overflowY: 'auto',
          mt: -1,
          display: 'grid',
          alignContent: 'start',
          gap: 0.25,
        }}
      >
        {conversations.map((conversation) => (
          <ListItemButton
            key={conversation.id}
            component={NavLink}
            to={`/c/${conversation.id}`}
            sx={{
              '&.active': {
                bgcolor: 'action.selected',
                '& .MuiListItemIcon-root': { color: 'primary.main' },
                '& .MuiListItemText-primary': { fontWeight: 600 },
              },
            }}
          >
            <ListItemIcon sx={{ minWidth: 32 }}>
              <ChatBubbleOutlineRounded fontSize="small" />
            </ListItemIcon>
            <ListItemText
              primary={conversation.title}
              slotProps={{ primary: { noWrap: true, title: conversation.title } }}
            />
          </ListItemButton>
        ))}
        {conversations.length === 0 && (
          <Typography
            variant="body2"
            color="text.secondary"
            component="li"
            sx={{ px: 1, py: 1, listStyle: 'none' }}
          >
            Your trips will show up here.
          </Typography>
        )}
      </List>

      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          p: 1,
          borderRadius: 3,
          border: 1,
          borderColor: 'divider',
        }}
      >
        <ButtonBase
          aria-label="Profile"
          onClick={() => setProfileOpen(true)}
          sx={{
            flexGrow: 1,
            minWidth: 0,
            justifyContent: 'flex-start',
            gap: 1.25,
            borderRadius: 2,
            p: 0.5,
          }}
        >
          <Avatar sx={{ width: 32, height: 32, fontSize: 14, bgcolor: 'primary.main' }}>
            {initials(me.name)}
          </Avatar>
          <Typography variant="body2" noWrap sx={{ fontWeight: 600 }}>
            {me.name}
          </Typography>
        </ButtonBase>
        <Tooltip title="Logout">
          <IconButton aria-label="Logout" size="small" onClick={handleLogout}>
            <LogoutRounded fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
      <ProfileDialog me={me} open={profileOpen} onClose={() => setProfileOpen(false)} />
    </Box>
  );
}
