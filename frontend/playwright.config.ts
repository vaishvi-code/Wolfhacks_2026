import { defineConfig, devices } from '@playwright/test'
import { fileURLToPath } from 'node:url'
export default defineConfig({
  testDir:'./e2e',fullyParallel:false,workers:1,timeout:45000,
  use:{baseURL:'http://127.0.0.1:4173',trace:'retain-on-failure'},
  projects:[{name:'desktop',use:{...devices['Desktop Chrome'],viewport:{width:1440,height:1000}}},{name:'mobile',use:{...devices['iPhone 13'],defaultBrowserType:'chromium'}}],
  webServer:[
    {command:'.venv/bin/python -m uvicorn survival_geo.api.app:app --host 127.0.0.1 --port 8000',cwd:fileURLToPath(new URL('../backend',import.meta.url)),url:'http://127.0.0.1:8000/api/health',reuseExistingServer:false,env:{WOLFHACKS_CONFIG:'',WOLFHACKS_DATA_DIR:'/private/tmp/wolfhacks-pwa-e2e'}},
    {command:'npm run preview -- --port 4173',url:'http://127.0.0.1:4173',reuseExistingServer:false},
  ],
})
