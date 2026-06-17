/**
 * Electron main process.
 *
 * 1. Spawns the Python API server as a child process.
 * 2. Waits for DBAIDE_PORT=<port> on stdout.
 * 3. Opens the renderer (Vue app) pointed at that port.
 */
const { app, BrowserWindow, ipcMain } = require('electron')
const { spawn } = require('child_process')
const path = require('path')

let mainWindow = null
let pythonProcess = null
let apiPort = null

const isDev = !app.isPackaged

function findPython() {
  // In development, use the project's venv
  const venvPython = path.join(__dirname, '..', '..', '..', '.venv', 'bin', 'python')
  if (require('fs').existsSync(venvPython)) return venvPython
  return 'python3'
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const python = findPython()
    const serverModule = path.join(__dirname, '..', '..', '..', 'dbaide', 'server', 'app.py')
    pythonProcess = spawn(python, [serverModule], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    })

    pythonProcess.stdout.on('data', (data) => {
      const line = data.toString().trim()
      const match = line.match(/DBAIDE_PORT=(\d+)/)
      if (match) {
        apiPort = parseInt(match[1], 10)
        console.log(`Backend started on port ${apiPort}`)
        resolve(apiPort)
      }
    })

    pythonProcess.stderr.on('data', (data) => {
      console.error(`[python] ${data.toString().trim()}`)
    })

    pythonProcess.on('error', reject)
    pythonProcess.on('exit', (code) => {
      if (!apiPort) reject(new Error(`Python exited with code ${code}`))
    })

    setTimeout(() => {
      if (!apiPort) reject(new Error('Backend startup timeout'))
    }, 30000)
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 12, y: 14 },
    backgroundColor: '#1a1a2e',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'dist', 'index.html'))
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// IPC: renderer queries the API port
ipcMain.handle('get-api-port', () => apiPort)

app.whenReady().then(async () => {
  try {
    await startBackend()
    createWindow()
  } catch (err) {
    console.error('Failed to start backend:', err)
    app.quit()
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (mainWindow === null) createWindow()
})

app.on('before-quit', () => {
  if (pythonProcess) {
    pythonProcess.kill()
    pythonProcess = null
  }
})
