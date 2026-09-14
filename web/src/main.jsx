import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import Analyse from './pages/Analyse.jsx'
import './styles.css'

// No router library. The app has exactly two screens and the second one (the agent layer's
// analysis view) is only ever reached by a fresh navigation — App.jsx opens it with
// `window.open(...)`, never a client-side link — so there is no in-app transition between them
// for a router to manage, and adding react-router-dom would buy nothing over reading the path
// once at startup. Vite's dev server falls back to index.html for any path that is not a real
// file, and a production static host configured the same way, so a hard load of `/analyse/E-000812`
// (a bookmark, a reload, a new tab) always reaches this same entry point and resolves correctly.
const path = window.location.pathname
const analyseMatch = path.match(/^\/analyse\/([^/]+)\/?$/)

createRoot(document.getElementById('root')).render(
  analyseMatch ? <Analyse episodeId={decodeURIComponent(analyseMatch[1])} /> : <App />,
)
