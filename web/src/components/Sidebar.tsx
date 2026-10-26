import { Box, Button, List, ListItemButton, ListItemText } from '@mui/material';
import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router';
import { type AppData, logout } from '../api/api';
import { ProfileDialog } from './ProfileDialog';

export function Sidebar({ me, conversations }: AppData) {
  const navigate = useNavigate();
  const [profileOpen, setProfileOpen] = useState(false);

  async function handleLogout() {
    await logout().catch(() => undefined);
    await navigate('/login');
  }

  return (
    <Box component="nav" sx={{ display: 'flex', flexDirection: 'column', height: '100%', p: 1 }}>
      <Button component={NavLink} to="/" variant="contained" sx={{ mb: 1 }}>
        New conversation
      </Button>
      <List aria-label="Conversation history" sx={{ flexGrow: 1, overflowY: 'auto' }}>
        {conversations.map((conversation) => (
          <ListItemButton key={conversation.id} component={NavLink} to={`/c/${conversation.id}`}>
            <ListItemText primary={conversation.title} />
          </ListItemButton>
        ))}
      </List>
      <Button onClick={() => setProfileOpen(true)}>Profile</Button>
      <Button onClick={handleLogout}>Logout</Button>
      <ProfileDialog me={me} open={profileOpen} onClose={() => setProfileOpen(false)} />
    </Box>
  );
}
