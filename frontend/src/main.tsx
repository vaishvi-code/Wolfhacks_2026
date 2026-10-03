import { Component, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
class ErrorBoundary extends Component<{children:ReactNode},{failed:boolean}> {
  state={failed:false}
  static getDerivedStateFromError(){return {failed:true}}
  render(){return this.state.failed?<main className="fatal" role="alert"><h1>Unable to display this view</h1><p>Reload to try again. Downloaded route data has not been deleted.</p><button onClick={()=>location.reload()}>Reload application</button></main>:this.props.children}
}
createRoot(document.getElementById('root')!).render(<ErrorBoundary><App/></ErrorBoundary>)
if(import.meta.env.PROD&&'serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{
  window.dispatchEvent(new CustomEvent('pwa-error'))
})
