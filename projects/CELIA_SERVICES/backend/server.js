require('dotenv').config();
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const compression = require('compression');
const morgan = require('morgan');
const http = require('http');
const socketIo = require('socket.io');
const rateLimit = require('express-rate-limit');
const MongoSanitize = require('express-mongo-sanitize');
const XSS = require('xss');
const HPP = require('hpp');

// Import middleware
const authMiddleware = require('./middleware/auth');
const errorHandler = require('./middleware/errorHandler');
const requestLogger = require('./middleware/requestLogger');
const metrics = require('./middleware/metrics');

// Import routes
const authRoutes = require('./routes/auth');
const agentsRoutes = require('./routes/agents');
const tasksRoutes = require('./routes/tasks');
const messagesRoutes = require('./routes/messages');
const analyticsRoutes = require('./routes/analytics');
const integrationsRoutes = require('./routes/integrations');
const securityRoutes = require('./routes/security');
const monitoringRoutes = require('./routes/monitoring');
const dashboardRoutes = require('./routes/dashboard');

// Import socket handlers
const socketHandlers = require('./sockets');

// Import services
const DatabaseService = require('./services/database');
const RedisService = require('./services/redis');
const AgentManager = require('./services/agentManager');
const NotificationService = require('./services/notification');

// Import agent engines
const CustomerSupportAgent = require('./agents/CustomerSupportAgent');
const ContentGeneratorAgent = require('./agents/ContentGeneratorAgent');
const AnalyticsAgent = require('./agents/AnalyticsAgent');
const MonitoringAgent = require('./agents/MonitoringAgent');
const SecurityAgent = require('./agents/SecurityAgent');
const IntegrationAgent = require('./agents/IntegrationAgent');
const LearningAgent = require('./agents/LearningAgent');
const UXAdvisorAgent = require('./agents/UXAdvisorAgent');
const DevOpsAgent = require('./agents/DevOpsAgent');
const OrchestratorAgent = require('./agents/OrchestratorAgent');

const app = express();
const server = http.createServer(app);
const io = socketIo(server, {
  cors: {
    origin: process.env.FRONTEND_URL || "http://localhost:3000",
    methods: ["GET", "POST"],
    credentials: true
  }
});

const PORT = process.env.PORT || 3001;

// Security middleware
app.use(helmet({
  crossOriginResourcePolicy: { policy: "cross-origin" }
}));
app.use(compression());
app.use(MongoSanitize());
app.use(HPP());
app.use(XSS());

// Rate limiting
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 100, // Limit each IP to 100 requests per windowMs
  message: {
    error: 'Too many requests from this IP, please try again later.'
  },
  standardHeaders: true,
  legacyHeaders: false,
});
app.use('/api/', limiter);

// CORS configuration
app.use(cors({
  origin: process.env.FRONTEND_URL || "http://localhost:3000",
  credentials: true,
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
  allowedHeaders: ['Content-Type', 'Authorization']
}));

// Body parsing middleware
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));

// Logging middleware
app.use(morgan('combined'));
app.use(requestLogger);

// Metrics middleware
app.use(metrics);

// Health check endpoint
app.get('/health', (req, res) => {
  res.status(200).json({
    status: 'OK',
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
    environment: process.env.NODE_ENV || 'development',
    version: process.env.npm_package_version || '1.0.0'
  });
});

// API Documentation
app.use('/api-docs', require('./routes/docs'));

// Routes
app.use('/api/auth', authRoutes);
app.use('/api/dashboard', authMiddleware, dashboardRoutes);
app.use('/api/agents', authMiddleware, agentsRoutes);
app.use('/api/tasks', authMiddleware, tasksRoutes);
app.use('/api/messages', authMiddleware, messagesRoutes);
app.use('/api/analytics', authMiddleware, analyticsRoutes);
app.use('/api/integrations', authMiddleware, integrationsRoutes);
app.use('/api/security', authMiddleware, securityRoutes);
app.use('/api/monitoring', authMiddleware, monitoringRoutes);

// Metrics endpoint for Prometheus
app.get('/metrics', (req, res) => {
  res.set('Content-Type', require('prom-client').register.contentType);
  res.end(require('prom-client').register.metrics());
});

// Socket.io connection handling
io.on('connection', (socket) => {
  console.log(`🔌 Client connected: ${socket.id}`);
  
  // Handle socket events
  socketHandlers(io, socket);
  
  socket.on('disconnect', () => {
    console.log(`🔌 Client disconnected: ${socket.id}`);
  });
  
  socket.on('error', (error) => {
    console.error(`❌ Socket error for ${socket.id}:`, error);
  });
});

// Initialize services
async function initializeServices() {
  try {
    console.log('🚀 Initializing services...');
    
    // Initialize database
    await DatabaseService.initialize();
    console.log('✅ Database connected');
    
    // Initialize Redis
    await RedisService.initialize();
    console.log('✅ Redis connected');
    
    // Initialize agent manager
    const agentManager = new AgentManager(io);
    
    // Initialize all agents
    console.log('🤖 Initializing agents...');
    
    const agents = [
      new CustomerSupportAgent(),
      new ContentGeneratorAgent(),
      new AnalyticsAgent(),
      new MonitoringAgent(),
      new SecurityAgent(),
      new IntegrationAgent(),
      new LearningAgent(),
      new UXAdvisorAgent(),
      new DevOpsAgent(),
      new OrchestratorAgent(agentManager)
    ];
    
    // Register agents
    await agentManager.registerAgents(agents);
    
    console.log('✅ All services initialized successfully');
    
    return { agentManager };
  } catch (error) {
    console.error('❌ Service initialization failed:', error);
    throw error;
  }
}

// Graceful shutdown
process.on('SIGTERM', async () => {
  console.log('🛑 SIGTERM received. Shutting down gracefully...');
  
  server.close(() => {
    console.log('✅ HTTP server closed');
  });
  
  // Close database connections
  await DatabaseService.close();
  await RedisService.close();
  
  process.exit(0);
});

process.on('SIGINT', async () => {
  console.log('🛑 SIGINT received. Shutting down gracefully...');
  
  server.close(() => {
    console.log('✅ HTTP server closed');
  });
  
  // Close database connections
  await DatabaseService.close();
  await RedisService.close();
  
  process.exit(0);
});

// Handle uncaught exceptions
process.on('uncaughtException', (error) => {
  console.error('❌ Uncaught Exception:', error);
  process.exit(1);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('❌ Unhandled Rejection at:', promise, 'reason:', reason);
  process.exit(1);
});

// Start server
async function startServer() {
  try {
    const { agentManager } = await initializeServices();
    
    server.listen(PORT, () => {
      console.log(`🌟 Agent System Server running on port ${PORT}`);
      console.log(`📊 Dashboard: http://localhost:${PORT}/dashboard`);
      console.log(`🔧 Health check: http://localhost:${PORT}/health`);
      console.log(`📖 API docs: http://localhost:${PORT}/api-docs`);
      console.log(`⚡ Environment: ${process.env.NODE_ENV || 'development'}`);
    });
    
    // Start background tasks
    await startBackgroundTasks(agentManager);
    
  } catch (error) {
    console.error('❌ Server startup failed:', error);
    process.exit(1);
  }
}

// Background tasks scheduler
async function startBackgroundTasks(agentManager) {
  const cron = require('node-cron');
  
  // Task queue processor
  cron.schedule('*/30 * * * * *', async () => {
    await agentManager.processTaskQueue();
  });
  
  // Agent health check
  cron.schedule('*/1 * * * *', async () => {
    await agentManager.checkAgentHealth();
  });
  
  // System monitoring
  cron.schedule('*/10 * * * *', async () => {
    await agentManager.runSystemMonitoring();
  });
  
  // Performance metrics collection
  cron.schedule('*/5 * * * *', async () => {
    await agentManager.collectPerformanceMetrics();
  });
  
  console.log('✅ Background tasks scheduled');
}

startServer();

module.exports = app;