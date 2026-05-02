import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard,
  Play,
  FileText,
  Library,
  Brain,
  Settings,
} from 'lucide-react'

const NAV_ITEMS = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/run', icon: Play, label: 'Run' },
  { to: '/reports', icon: FileText, label: 'Reports' },
  { to: '/papers', icon: Library, label: 'Papers' },
  { to: '/profiles', icon: Brain, label: 'Profiles' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function Layout() {
  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <aside className="w-56 border-r border-border bg-sidebar flex flex-col">
        <div className="p-4 border-b border-border">
          <h1 className="text-lg font-bold text-sidebar-foreground">Paper Agents</h1>
          <p className="text-xs text-muted-foreground mt-0.5">Academic Paper Analysis</p>
        </div>
        <nav className="flex-1 p-2 space-y-0.5">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-4 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                    : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
                }`
              }
            >
              <Icon className="w-4 h-4" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-auto">
        <div className="p-6">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
