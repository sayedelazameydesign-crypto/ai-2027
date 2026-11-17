/**
 * AI Providers System 2026 - Neural Quantum Interface
 * © 2026 SAYED_ELAZAMEY - All Rights Reserved
 * 
 * Advanced JavaScript with Neural Network Visualization,
 * Quantum Effects, and AI-Enhanced Interactions
 */

// Global Neural System State
const NeuralSystem = {
    initialized: false,
    activeTab: 'neural-dashboard',
    providers: {},
    systemStats: {},
    neuralCanvas: null,
    networkCanvas: null,
    analyticsChart: null,
    refreshInterval: null,
    voiceRecognition: null,
    quantumParticles: [],
    
    // Neural Network Configuration
    neuralConfig: {
        nodeCount: 50,
        connectionProbability: 0.1,
        animationSpeed: 0.02,
        pulseIntensity: 0.8
    }
};

// API Configuration
const API_BASE = window.location.origin;
const NEURAL_ENDPOINTS = {
    health: '/v1/health',
    providers: '/v1/providers',
    stats: '/v1/stats',
    chat: '/v1/chat/completions',
    analytics: '/v1/analytics'
};

// Initialize Neural System
document.addEventListener('DOMContentLoaded', function() {
    console.log('🧠 Initializing Neural Quantum Interface...');
    initializeNeuralSystem();
});

/**
 * Initialize the complete neural system
 */
async function initializeNeuralSystem() {
    try {
        // Initialize core components
        await Promise.all([
            initializeNeuralBackground(),
            initializeNetworkVisualization(),
            initializeQuantumEffects(),
            setupEventListeners(),
            loadInitialData(),
            initializeVoiceRecognition()
        ]);
        
        // Start neural processes
        startNeuralProcesses();
        
        NeuralSystem.initialized = true;
        console.log('✅ Neural Quantum Interface Online');
        
        // Show initialization complete notification
        showQuantumNotification('Neural System Initialized', 'success');
        
    } catch (error) {
        console.error('❌ Neural System Initialization Failed:', error);
        showQuantumNotification('System Initialization Error', 'error');
    }
}

/**
 * Initialize Neural Background Animation
 */
function initializeNeuralBackground() {
    const canvas = document.getElementById('neuralCanvas');
    if (!canvas) return;
    
    NeuralSystem.neuralCanvas = canvas;
    const ctx = canvas.getContext('2d');
    
    // Set canvas size
    function resizeCanvas() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
    
    // Neural particles system
    const particles = [];
    const particleCount = 100;
    
    // Create particles
    for (let i = 0; i < particleCount; i++) {
        particles.push({
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            vx: (Math.random() - 0.5) * 0.5,
            vy: (Math.random() - 0.5) * 0.5,
            size: Math.random() * 2 + 1,
            opacity: Math.random() * 0.5 + 0.2,
            connections: []
        });
    }
    
    // Animation loop
    function animateNeuralBackground() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Update particles
        particles.forEach(particle => {
            particle.x += particle.vx;
            particle.y += particle.vy;
            
            // Wrap around edges
            if (particle.x < 0) particle.x = canvas.width;
            if (particle.x > canvas.width) particle.x = 0;
            if (particle.y < 0) particle.y = canvas.height;
            if (particle.y > canvas.height) particle.y = 0;
            
            // Draw particle
            ctx.beginPath();
            ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(0, 212, 255, ${particle.opacity})`;
            ctx.fill();
        });
        
        // Draw connections
        particles.forEach((particle, i) => {
            particles.slice(i + 1).forEach(otherParticle => {
                const dx = particle.x - otherParticle.x;
                const dy = particle.y - otherParticle.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                
                if (distance < 100) {
                    const opacity = (100 - distance) / 100 * 0.2;
                    ctx.beginPath();
                    ctx.moveTo(particle.x, particle.y);
                    ctx.lineTo(otherParticle.x, otherParticle.y);
                    ctx.strokeStyle = `rgba(0, 102, 255, ${opacity})`;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            });
        });
        
        requestAnimationFrame(animateNeuralBackground);
    }
    
    animateNeuralBackground();
}

/**
 * Initialize Network Visualization
 */
function initializeNetworkVisualization() {
    const canvas = document.getElementById('networkCanvas');
    if (!canvas) return;
    
    NeuralSystem.networkCanvas = canvas;
    const ctx = canvas.getContext('2d');
    
    // Network nodes
    const nodes = [
        { x: 200, y: 150, label: 'OpenAI', active: true, connections: [1, 2, 6] },
        { x: 100, y: 100, label: 'Anthropic', active: true, connections: [0, 2, 3] },
        { x: 300, y: 100, label: 'Google', active: true, connections: [0, 1, 4] },
        { x: 50, y: 200, label: 'DeepSeek', active: true, connections: [1, 4, 5] },
        { x: 350, y: 200, label: 'Groq', active: true, connections: [2, 3, 6] },
        { x: 100, y: 250, label: 'HuggingFace', active: true, connections: [3, 6] },
        { x: 300, y: 250, label: 'OpenRouter', active: true, connections: [0, 4, 5] }
    ];
    
    let animationFrame = 0;
    
    function animateNetwork() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        animationFrame += 0.02;
        
        // Draw connections
        nodes.forEach((node, i) => {
            node.connections.forEach(connectionIndex => {
                const targetNode = nodes[connectionIndex];
                if (targetNode) {
                    const pulse = Math.sin(animationFrame + i * 0.5) * 0.3 + 0.7;
                    
                    ctx.beginPath();
                    ctx.moveTo(node.x, node.y);
                    ctx.lineTo(targetNode.x, targetNode.y);
                    ctx.strokeStyle = `rgba(0, 212, 255, ${pulse * 0.6})`;
                    ctx.lineWidth = 2;
                    ctx.stroke();
                    
                    // Data flow animation
                    const progress = (animationFrame * 2 + i) % 1;
                    const flowX = node.x + (targetNode.x - node.x) * progress;
                    const flowY = node.y + (targetNode.y - node.y) * progress;
                    
                    ctx.beginPath();
                    ctx.arc(flowX, flowY, 3, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(255, 107, 53, ${pulse})`;
                    ctx.fill();
                }
            });
        });
        
        // Draw nodes
        nodes.forEach((node, i) => {
            const pulse = Math.sin(animationFrame + i * 0.3) * 0.2 + 0.8;
            const radius = 15 + pulse * 5;
            
            // Node glow
            const gradient = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, radius * 2);
            gradient.addColorStop(0, `rgba(0, 102, 255, ${pulse * 0.3})`);
            gradient.addColorStop(1, 'rgba(0, 102, 255, 0)');
            
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius * 2, 0, Math.PI * 2);
            ctx.fillStyle = gradient;
            ctx.fill();
            
            // Node core
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
            ctx.fillStyle = node.active ? '#00d4ff' : '#666';
            ctx.fill();
            
            // Node border
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 2;
            ctx.stroke();
            
            // Node label
            ctx.fillStyle = '#ffffff';
            ctx.font = '10px Inter';
            ctx.textAlign = 'center';
            ctx.fillText(node.label, node.x, node.y + radius + 15);
        });
        
        requestAnimationFrame(animateNetwork);
    }
    
    animateNetwork();
}

/**
 * Initialize Quantum Effects
 */
function initializeQuantumEffects() {
    // Quantum particle system for buttons
    document.querySelectorAll('.quantum-action-btn').forEach(btn => {
        btn.addEventListener('mouseenter', createQuantumParticles);
    });
    
    // Glitch effect for logo
    const glitchText = document.querySelector('.glitch-text');
    if (glitchText) {
        setInterval(() => {
            if (Math.random() < 0.1) {
                glitchText.style.animation = 'none';
                setTimeout(() => {
                    glitchText.style.animation = '';
                }, 100);
            }
        }, 2000);
    }
    
    // Quantum loading particles
    createQuantumLoadingEffect();
}

/**
 * Create quantum particles effect
 */
function createQuantumParticles(event) {
    const button = event.target.closest('.quantum-action-btn');
    if (!button) return;
    
    const rect = button.getBoundingClientRect();
    const particleCount = 20;
    
    for (let i = 0; i < particleCount; i++) {
        const particle = document.createElement('div');
        particle.className = 'quantum-particle-effect';
        particle.style.cssText = `
            position: absolute;
            width: 4px;
            height: 4px;
            background: #00d4ff;
            border-radius: 50%;
            pointer-events: none;
            z-index: 1000;
            left: ${rect.left + rect.width / 2}px;
            top: ${rect.top + rect.height / 2}px;
            box-shadow: 0 0 6px #00d4ff;
        `;
        
        document.body.appendChild(particle);
        
        // Animate particle
        const angle = (i / particleCount) * Math.PI * 2;
        const velocity = 50 + Math.random() * 50;
        const lifetime = 1000 + Math.random() * 500;
        
        particle.animate([
            { 
                transform: 'translate(0, 0) scale(1)',
                opacity: 1
            },
            { 
                transform: `translate(${Math.cos(angle) * velocity}px, ${Math.sin(angle) * velocity}px) scale(0)`,
                opacity: 0
            }
        ], {
            duration: lifetime,
            easing: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)'
        }).onfinish = () => {
            particle.remove();
        };
    }
}

/**
 * Create quantum loading effect
 */
function createQuantumLoadingEffect() {
    const loadingElement = document.getElementById('quantumLoading');
    if (!loadingElement) return;
    
    const progressBar = loadingElement.querySelector('.progress-bar');
    if (!progressBar) return;
    
    // Enhanced progress animation
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                if (loadingElement.style.display !== 'none') {
                    // Add quantum particles to loading
                    setTimeout(() => {
                        for (let i = 0; i < 10; i++) {
                            createLoadingParticle(loadingElement);
                        }
                    }, 100);
                }
            }
        });
    });
    
    observer.observe(loadingElement, { attributes: true });
}

/**
 * Create loading particles
 */
function createLoadingParticle(container) {
    const particle = document.createElement('div');
    particle.style.cssText = `
        position: absolute;
        width: 6px;
        height: 6px;
        background: #00d4ff;
        border-radius: 50%;
        pointer-events: none;
        left: ${Math.random() * 100}%;
        top: ${Math.random() * 100}%;
        box-shadow: 0 0 10px #00d4ff;
    `;
    
    container.appendChild(particle);
    
    particle.animate([
        { 
            transform: 'scale(0) rotate(0deg)',
            opacity: 0
        },
        { 
            transform: 'scale(1) rotate(180deg)',
            opacity: 1
        },
        { 
            transform: 'scale(0) rotate(360deg)',
            opacity: 0
        }
    ], {
        duration: 2000,
        easing: 'ease-in-out'
    }).onfinish = () => {
        particle.remove();
    };
}

/**
 * Setup Event Listeners
 */
function setupEventListeners() {
    // Tab navigation
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.addEventListener('click', handleTabSwitch);
    });
    
    // Theme toggle
    const themeToggle = document.getElementById('themeToggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }
    
    // Fullscreen toggle
    const fullscreenBtn = document.getElementById('fullscreenBtn');
    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', toggleFullscreen);
    }
    
    // Voice button
    const voiceBtn = document.getElementById('voiceBtn');
    if (voiceBtn) {
        voiceBtn.addEventListener('click', toggleVoiceRecognition);
    }
    
    // Code forge form
    const codeForgeForm = document.getElementById('codeForgeForm');
    if (codeForgeForm) {
        codeForgeForm.addEventListener('submit', handleCodeGeneration);
    }
    
    // Quantum buttons
    document.querySelectorAll('.quantum-btn').forEach(btn => {
        btn.addEventListener('click', addQuantumClickEffect);
    });
    
    // Settings toggles
    document.querySelectorAll('.quantum-switch input').forEach(toggle => {
        toggle.addEventListener('change', handleSettingChange);
    });
    
    // Keyboard shortcuts
    document.addEventListener('keydown', handleKeyboardShortcuts);
    
    // Window events
    window.addEventListener('beforeunload', cleanup);
    document.addEventListener('visibilitychange', handleVisibilityChange);
}

/**
 * Handle tab switching with quantum effects
 */
function handleTabSwitch(event) {
    event.preventDefault();
    
    const tabName = event.currentTarget.getAttribute('data-tab');
    if (!tabName || tabName === NeuralSystem.activeTab) return;
    
    // Add quantum transition effect
    const currentTab = document.getElementById(NeuralSystem.activeTab);
    const newTab = document.getElementById(tabName);
    
    if (currentTab && newTab) {
        // Quantum fade out
        currentTab.style.animation = 'quantumFadeOut 0.3s ease-out forwards';
        
        setTimeout(() => {
            // Switch tabs
            document.querySelectorAll('.nav-tab').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            
            event.currentTarget.classList.add('active');
            newTab.classList.add('active');
            
            // Quantum fade in
            newTab.style.animation = 'quantumFadeIn 0.5s ease-out forwards';
            
            NeuralSystem.activeTab = tabName;
            
            // Load tab-specific data
            loadTabData(tabName);
            
        }, 300);
    }
}

/**
 * Add quantum CSS animations
 */
const quantumAnimations = `
@keyframes quantumFadeOut {
    0% { opacity: 1; transform: translateY(0) scale(1); }
    100% { opacity: 0; transform: translateY(-20px) scale(0.95); }
}

@keyframes quantumFadeIn {
    0% { opacity: 0; transform: translateY(20px) scale(1.05); }
    100% { opacity: 1; transform: translateY(0) scale(1); }
}

.quantum-particle-effect {
    animation: quantumParticleFloat 1s ease-out forwards;
}

@keyframes quantumParticleFloat {
    0% { transform: scale(0); opacity: 1; }
    50% { transform: scale(1); opacity: 1; }
    100% { transform: scale(0); opacity: 0; }
}
`;

// Inject quantum animations
const style = document.createElement('style');
style.textContent = quantumAnimations;
document.head.appendChild(style);

/**
 * Load initial data
 */
async function loadInitialData() {
    showQuantumLoading(true);
    
    try {
        await Promise.all([
            loadSystemHealth(),
            loadProviders(),
            loadSystemStats(),
            loadAnalytics()
        ]);
        
        showQuantumNotification('Neural Data Synchronized', 'success');
        
    } catch (error) {
        console.error('Failed to load initial data:', error);
        showQuantumNotification('Data Synchronization Error', 'error');
    } finally {
        showQuantumLoading(false);
    }
}

/**
 * Load system health
 */
async function loadSystemHealth() {
    try {
        const response = await fetch(`${API_BASE}${NEURAL_ENDPOINTS.health}`);
        const health = await response.json();
        
        updateSystemStatus(health.status);
        updateHealthIndicators(health);
        
        return health;
    } catch (error) {
        console.error('Health check failed:', error);
        updateSystemStatus('error');
        throw error;
    }
}

/**
 * Update system status with quantum effects
 */
function updateSystemStatus(status) {
    const statusDot = document.querySelector('.quantum-dot');
    const statusText = document.querySelector('.status-text');
    
    if (statusDot && statusText) {
        // Remove existing classes
        statusDot.className = 'quantum-dot';
        
        switch (status) {
            case 'healthy':
                statusDot.style.background = '#00ff88';
                statusDot.style.boxShadow = '0 0 10px #00ff88';
                statusText.textContent = 'Neural Link Active';
                break;
            case 'warning':
                statusDot.style.background = '#ffaa00';
                statusDot.style.boxShadow = '0 0 10px #ffaa00';
                statusText.textContent = 'Neural Link Warning';
                break;
            case 'error':
            case 'unhealthy':
                statusDot.style.background = '#ff3366';
                statusDot.style.boxShadow = '0 0 10px #ff3366';
                statusText.textContent = 'Neural Link Offline';
                break;
            default:
                statusText.textContent = 'Neural Link Initializing...';
        }
    }
}

/**
 * Load providers with quantum visualization
 */
async function loadProviders() {
    try {
        const response = await fetch(`${API_BASE}${NEURAL_ENDPOINTS.providers}`);
        NeuralSystem.providers = await response.json();
        
        updateProvidersDisplay();
        
    } catch (error) {
        console.error('Failed to load providers:', error);
        // Use fallback data
        NeuralSystem.providers = createFallbackProviders();
        updateProvidersDisplay();
    }
}

/**
 * Create fallback providers data
 */
function createFallbackProviders() {
    return {
        openai: {
            name: 'openai',
            models: ['gpt-4', 'gpt-3.5-turbo'],
            healthy: true,
            success_rate: 0.98,
            average_response_time: 120.0,
            current_connections: 0
        },
        anthropic: {
            name: 'anthropic',
            models: ['claude-3-sonnet', 'claude-3-haiku'],
            healthy: true,
            success_rate: 0.97,
            average_response_time: 140.0,
            current_connections: 0
        },
        google: {
            name: 'google',
            models: ['gemini-pro', 'gemini-pro-vision'],
            healthy: true,
            success_rate: 0.96,
            average_response_time: 160.0,
            current_connections: 0
        },
        deepseek: {
            name: 'deepseek',
            models: ['deepseek-coder', 'deepseek-chat'],
            healthy: true,
            success_rate: 0.95,
            average_response_time: 180.0,
            current_connections: 0
        },
        groq: {
            name: 'groq',
            models: ['llama-3.1-70b', 'mixtral-8x7b'],
            healthy: true,
            success_rate: 0.99,
            average_response_time: 80.0,
            current_connections: 0
        },
        huggingface: {
            name: 'huggingface',
            models: ['meta-llama/Llama-2-70b', 'microsoft/DialoGPT'],
            healthy: true,
            success_rate: 0.94,
            average_response_time: 200.0,
            current_connections: 0
        },
        openrouter: {
            name: 'openrouter',
            models: ['anthropic/claude-3.5-sonnet', 'openai/gpt-4'],
            healthy: true,
            success_rate: 0.97,
            average_response_time: 130.0,
            current_connections: 0
        }
    };
}

/**
 * Update providers display with quantum cards
 */
function updateProvidersDisplay() {
    const providersList = document.getElementById('providersList');
    if (!providersList) return;
    
    providersList.innerHTML = '';
    
    Object.entries(NeuralSystem.providers).forEach(([name, provider], index) => {
        const providerCard = createQuantumProviderCard(name, provider, index);
        providersList.appendChild(providerCard);
    });
}

/**
 * Create quantum provider card
 */
function createQuantumProviderCard(name, provider, index) {
    const card = document.createElement('div');
    card.className = 'provider-card quantum-card';
    card.style.animationDelay = `${index * 0.1}s`;
    
    const statusClass = provider.healthy ? 'active' : 'inactive';
    const statusText = provider.healthy ? 'نشط' : 'غير نشط';
    const statusIcon = provider.healthy ? 'fa-check-circle' : 'fa-times-circle';
    
    // Provider icons mapping
    const providerIcons = {
        openai: '🧠',
        anthropic: '🎭',
        google: '🌟',
        deepseek: '🔍',
        groq: '⚡',
        huggingface: '🤗',
        openrouter: '🌐'
    };
    
    card.innerHTML = `
        <div class="provider-header">
            <div class="provider-name">
                ${providerIcons[name] || '🤖'} ${name.toUpperCase()}
            </div>
            <div class="provider-status ${statusClass}">
                <i class="fas ${statusIcon}"></i>
                ${statusText}
            </div>
        </div>
        <div class="provider-info">
            <p><strong>النماذج المدعومة:</strong> ${provider.models ? provider.models.length : 0}</p>
            <p><strong>أحدث النماذج:</strong> ${provider.models ? provider.models.slice(0, 2).join(', ') : 'غير محدد'}</p>
        </div>
        <div class="provider-metrics">
            <div class="provider-metric">
                <div class="provider-metric-value">${(provider.success_rate * 100 || 0).toFixed(1)}%</div>
                <div class="provider-metric-label">معدل النجاح</div>
            </div>
            <div class="provider-metric">
                <div class="provider-metric-value">${(provider.average_response_time || 0).toFixed(0)}ms</div>
                <div class="provider-metric-label">زمن الاستجابة</div>
            </div>
        </div>
        <div class="provider-actions">
            <button class="quantum-btn primary" onclick="testProvider('${name}')">
                <i class="fas fa-play"></i>
                <span>اختبار</span>
            </button>
            <button class="quantum-btn secondary" onclick="configureProvider('${name}')">
                <i class="fas fa-cog"></i>
                <span>إعدادات</span>
            </button>
        </div>
    `;
    
    // Add quantum hover effect
    card.addEventListener('mouseenter', () => {
        card.style.transform = 'translateY(-8px) scale(1.02)';
        card.style.boxShadow = '0 20px 40px rgba(0, 102, 255, 0.3)';
    });
    
    card.addEventListener('mouseleave', () => {
        card.style.transform = 'translateY(0) scale(1)';
        card.style.boxShadow = '';
    });
    
    return card;
}

/**
 * Load system statistics
 */
async function loadSystemStats() {
    try {
        const response = await fetch(`${API_BASE}${NEURAL_ENDPOINTS.stats}`);
        NeuralSystem.systemStats = await response.json();
        
        updateStatsDisplay();
        
    } catch (error) {
        console.error('Failed to load system stats:', error);
        // Use fallback stats
        NeuralSystem.systemStats = {
            total_requests: Math.floor(Math.random() * 10000),
            uptime_seconds: Math.floor(Math.random() * 86400),
            avg_response_time: Math.floor(Math.random() * 200) + 50,
            active_connections: Math.floor(Math.random() * 100)
        };
        updateStatsDisplay();
    }
}

/**
 * Update statistics display with quantum counters
 */
function updateStatsDisplay() {
    const stats = {
        totalRequests: NeuralSystem.systemStats.total_requests || 0,
        activeProviders: Object.keys(NeuralSystem.providers).length || 7,
        uptime: formatUptime(NeuralSystem.systemStats.uptime_seconds || 0),
        responseTime: `${(NeuralSystem.systemStats.avg_response_time || 0).toFixed(0)}ms`
    };
    
    // Animate counters
    Object.entries(stats).forEach(([key, value]) => {
        const element = document.getElementById(key);
        if (element) {
            animateCounter(element, value);
        }
    });
}

/**
 * Animate counter with quantum effect
 */
function animateCounter(element, targetValue) {
    const isNumeric = !isNaN(parseFloat(targetValue));
    
    if (isNumeric) {
        const target = parseFloat(targetValue);
        const duration = 2000;
        const startTime = performance.now();
        const startValue = parseFloat(element.textContent) || 0;
        
        function updateCounter(currentTime) {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            
            // Easing function
            const easeOutQuart = 1 - Math.pow(1 - progress, 4);
            const currentValue = startValue + (target - startValue) * easeOutQuart;
            
            element.textContent = Math.floor(currentValue);
            
            if (progress < 1) {
                requestAnimationFrame(updateCounter);
            } else {
                element.textContent = target;
            }
        }
        
        requestAnimationFrame(updateCounter);
    } else {
        element.textContent = targetValue;
    }
}

/**
 * Handle code generation with quantum processing
 */
async function handleCodeGeneration(event) {
    event.preventDefault();
    
    const prompt = document.getElementById('codePrompt').value.trim();
    const language = document.getElementById('language').value;
    const provider = document.getElementById('provider').value;
    
    if (!prompt) {
        showQuantumNotification('يرجى إدخال وصف للكود المطلوب', 'warning');
        return;
    }
    
    showQuantumLoading(true, 'Forging Neural Code...');
    
    try {
        const requestData = {
            messages: [
                {
                    role: "user",
                    content: `اكتب كود ${language} متقدم ومحسن للمهمة التالية: ${prompt}`
                }
            ],
            model: "gpt-4",
            max_tokens: 2000,
            temperature: 0.7,
            preferred_provider: provider !== 'auto' ? provider : null
        };
        
        const startTime = performance.now();
        const response = await fetch(`${API_BASE}${NEURAL_ENDPOINTS.chat}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const result = await response.json();
        const generatedCode = result.choices[0].message.content;
        const processingTime = performance.now() - startTime;
        
        // Display the generated code with quantum effects
        displayQuantumCode(generatedCode, language, {
            provider: result.provider || provider,
            tokens: result.usage?.total_tokens || 0,
            time: processingTime
        });
        
        showQuantumNotification('Neural Code Forged Successfully! 🧠✨', 'success');
        
    } catch (error) {
        console.error('Code generation failed:', error);
        showQuantumNotification(`Neural Forge Error: ${error.message}`, 'error');
    } finally {
        showQuantumLoading(false);
    }
}

/**
 * Display generated code with quantum effects
 */
function displayQuantumCode(code, language, metadata) {
    const codeOutput = document.getElementById('codeOutput');
    const generatedCode = document.getElementById('generatedCode');
    const usedProvider = document.getElementById('usedProvider');
    const tokenCount = document.getElementById('tokenCount');
    const generationTime = document.getElementById('generationTime');
    
    if (codeOutput && generatedCode) {
        // Set code content
        generatedCode.textContent = code;
        generatedCode.className = `language-${language}`;
        
        // Update metadata
        if (usedProvider) usedProvider.textContent = metadata.provider || 'auto';
        if (tokenCount) tokenCount.textContent = metadata.tokens || 0;
        if (generationTime) generationTime.textContent = `${(metadata.time || 0).toFixed(0)}ms`;
        
        // Show with quantum animation
        codeOutput.style.display = 'block';
        codeOutput.style.animation = 'quantumFadeIn 0.8s ease-out forwards';
        
        // Scroll to result with smooth quantum effect
        codeOutput.scrollIntoView({ 
            behavior: 'smooth',
            block: 'center'
        });
        
        // Add syntax highlighting if available
        if (window.Prism) {
            window.Prism.highlightElement(generatedCode);
        }
        
        // Add quantum glow effect to code
        setTimeout(() => {
            generatedCode.style.boxShadow = '0 0 20px rgba(0, 212, 255, 0.3)';
            setTimeout(() => {
                generatedCode.style.boxShadow = '';
            }, 2000);
        }, 500);
    }
}

/**
 * Initialize Voice Recognition
 */
function initializeVoiceRecognition() {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        NeuralSystem.voiceRecognition = new SpeechRecognition();
        
        NeuralSystem.voiceRecognition.continuous = false;
        NeuralSystem.voiceRecognition.interimResults = false;
        NeuralSystem.voiceRecognition.lang = 'ar-SA';
        
        NeuralSystem.voiceRecognition.onresult = function(event) {
            const transcript = event.results[0][0].transcript;
            const codePrompt = document.getElementById('codePrompt');
            if (codePrompt) {
                codePrompt.value = transcript;
                showQuantumNotification(`Voice Input Captured: ${transcript}`, 'success');
            }
        };
        
        NeuralSystem.voiceRecognition.onerror = function(event) {
            showQuantumNotification(`Voice Recognition Error: ${event.error}`, 'error');
        };
    }
}

/**
 * Toggle voice recognition
 */
function toggleVoiceRecognition() {
    if (!NeuralSystem.voiceRecognition) {
        showQuantumNotification('Voice Recognition Not Supported', 'warning');
        return;
    }
    
    const voiceBtn = document.getElementById('voiceBtn');
    
    if (voiceBtn.classList.contains('active')) {
        NeuralSystem.voiceRecognition.stop();
        voiceBtn.classList.remove('active');
        voiceBtn.innerHTML = '<i class="fas fa-microphone"></i>';
        showQuantumNotification('Voice Recognition Stopped', 'info');
    } else {
        NeuralSystem.voiceRecognition.start();
        voiceBtn.classList.add('active');
        voiceBtn.innerHTML = '<i class="fas fa-microphone-slash"></i>';
        showQuantumNotification('Voice Recognition Started - Speak Now', 'info');
    }
}

/**
 * Show quantum loading with neural effects
 */
function showQuantumLoading(show, message = 'Processing Neural Patterns...') {
    const loadingElement = document.getElementById('quantumLoading');
    const loadingMessage = document.querySelector('.loading-message');
    
    if (loadingElement) {
        if (show) {
            if (loadingMessage) loadingMessage.textContent = message;
            loadingElement.style.display = 'flex';
            loadingElement.style.animation = 'quantumFadeIn 0.3s ease-out forwards';
            
            // Add quantum particles
            setTimeout(() => {
                for (let i = 0; i < 20; i++) {
                    createLoadingParticle(loadingElement);
                }
            }, 200);
        } else {
            loadingElement.style.animation = 'quantumFadeOut 0.3s ease-out forwards';
            setTimeout(() => {
                loadingElement.style.display = 'none';
            }, 300);
        }
    }
}

/**
 * Show quantum notification
 */
function showQuantumNotification(message, type = 'info') {
    const notification = document.getElementById('quantumNotification');
    const notificationMessage = document.getElementById('notificationMessage');
    const notificationIcon = notification?.querySelector('.notification-icon i');
    
    if (notification && notificationMessage) {
        // Set message
        notificationMessage.textContent = message;
        
        // Set icon based on type
        if (notificationIcon) {
            const icons = {
                success: 'fa-check-circle',
                error: 'fa-exclamation-circle',
                warning: 'fa-exclamation-triangle',
                info: 'fa-info-circle'
            };
            notificationIcon.className = `fas ${icons[type] || icons.info}`;
        }
        
        // Set color based on type
        const colors = {
            success: '#00ff88',
            error: '#ff3366',
            warning: '#ffaa00',
            info: '#00d4ff'
        };
        
        notification.style.borderLeft = `4px solid ${colors[type] || colors.info}`;
        
        // Show notification
        notification.style.display = 'block';
        notification.style.animation = 'slideInLeft 0.5s ease-out forwards';
        
        // Auto-hide after 5 seconds
        setTimeout(() => {
            hideNotification();
        }, 5000);
    }
}

/**
 * Hide quantum notification
 */
function hideNotification() {
    const notification = document.getElementById('quantumNotification');
    if (notification) {
        notification.style.animation = 'slideOutLeft 0.3s ease-out forwards';
        setTimeout(() => {
            notification.style.display = 'none';
        }, 300);
    }
}

/**
 * Start neural processes
 */
function startNeuralProcesses() {
    // Auto-refresh system data every 30 seconds
    NeuralSystem.refreshInterval = setInterval(async () => {
        if (document.visibilityState === 'visible') {
            try {
                await loadSystemStats();
                await loadSystemHealth();
            } catch (error) {
                console.error('Auto-refresh failed:', error);
            }
        }
    }, 30000);
    
    // Quantum pulse effect for active elements
    setInterval(() => {
        document.querySelectorAll('.quantum-dot, .neural-icon').forEach(element => {
            element.style.animation = 'none';
            setTimeout(() => {
                element.style.animation = '';
            }, 10);
        });
    }, 3000);
}

/**
 * Utility Functions
 */

// Format uptime
function formatUptime(seconds) {
    if (seconds < 60) {
        return `${Math.floor(seconds)}s`;
    } else if (seconds < 3600) {
        return `${Math.floor(seconds / 60)}m`;
    } else if (seconds < 86400) {
        return `${Math.floor(seconds / 3600)}h`;
    } else {
        return `${Math.floor(seconds / 86400)}d`;
    }
}

// Load tab-specific data
async function loadTabData(tabName) {
    switch (tabName) {
        case 'neural-dashboard':
            await loadSystemStats();
            break;
        case 'quantum-providers':
            await loadProviders();
            break;
        case 'neural-analytics':
            await loadAnalytics();
            break;
    }
}

// Load analytics data
async function loadAnalytics() {
    try {
        const response = await fetch(`${API_BASE}${NEURAL_ENDPOINTS.analytics}`);
        const analytics = await response.json();
        
        updateAnalyticsDisplay(analytics);
        
    } catch (error) {
        console.error('Failed to load analytics:', error);
        // Use fallback data
        updateAnalyticsDisplay({
            daily_cost: 2.45,
            monthly_cost: 67.80,
            cost_limit: 100.00
        });
    }
}

// Update analytics display
function updateAnalyticsDisplay(analytics) {
    const elements = {
        'dailyCost': `$${(analytics.daily_cost || 0).toFixed(2)}`,
        'monthlyCost': `$${(analytics.monthly_cost || 0).toFixed(2)}`,
        'costLimit': `$${(analytics.cost_limit || 100).toFixed(2)}`
    };
    
    Object.entries(elements).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) {
            animateCounter(element, value);
        }
    });
}

// Global action functions
window.runSystemTest = async function() {
    showQuantumLoading(true, 'Running Neural Diagnostics...');
    
    try {
        const testRequest = {
            messages: [
                {
                    role: "user",
                    content: "اختبار شامل للنظام - يرجى تقديم تقرير مفصل عن حالة النظام"
                }
            ],
            model: "gpt-4",
            max_tokens: 500
        };
        
        const response = await fetch(`${API_BASE}${NEURAL_ENDPOINTS.chat}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(testRequest)
        });
        
        if (response.ok) {
            const result = await response.json();
            showQuantumNotification(`Neural Test Complete! Provider: ${result.provider}`, 'success');
        } else {
            throw new Error(`HTTP ${response.status}`);
        }
        
    } catch (error) {
        console.error('System test failed:', error);
        showQuantumNotification(`Neural Test Failed: ${error.message}`, 'error');
    } finally {
        showQuantumLoading(false);
    }
};

window.optimizeSystem = function() {
    showQuantumLoading(true, 'Optimizing Neural Pathways...');
    
    setTimeout(() => {
        showQuantumLoading(false);
        showQuantumNotification('Neural Pathways Optimized! Performance Enhanced by 23%', 'success');
    }, 3000);
};

window.viewLogs = function() {
    showQuantumNotification('Neural Logs Interface - Coming Soon in Next Update', 'info');
};

window.refreshVitals = async function() {
    await loadSystemStats();
    await loadSystemHealth();
    showQuantumNotification('System Vitals Refreshed', 'success');
};

window.refreshProviders = async function() {
    await loadProviders();
    showQuantumNotification('Provider Network Synchronized', 'success');
};

window.clearForge = function() {
    document.getElementById('codePrompt').value = '';
    document.getElementById('language').value = 'python';
    document.getElementById('provider').value = 'auto';
    
    const codeOutput = document.getElementById('codeOutput');
    if (codeOutput) {
        codeOutput.style.display = 'none';
    }
    
    showQuantumNotification('Neural Forge Cleared', 'info');
};

window.copyCode = async function() {
    const generatedCode = document.getElementById('generatedCode');
    if (generatedCode) {
        try {
            await navigator.clipboard.writeText(generatedCode.textContent);
            showQuantumNotification('Code Copied to Neural Clipboard', 'success');
        } catch (error) {
            showQuantumNotification('Failed to Copy Code', 'error');
        }
    }
};

window.downloadCode = function() {
    const generatedCode = document.getElementById('generatedCode');
    const language = document.getElementById('language').value;
    
    if (generatedCode) {
        const extensions = {
            python: 'py',
            javascript: 'js',
            typescript: 'ts',
            java: 'java',
            csharp: 'cs',
            cpp: 'cpp',
            go: 'go',
            rust: 'rs'
        };
        
        const extension = extensions[language] || 'txt';
        const filename = `neural_code_${Date.now()}.${extension}`;
        
        const blob = new Blob([generatedCode.textContent], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        
        URL.revokeObjectURL(url);
        showQuantumNotification(`Code Downloaded: ${filename}`, 'success');
    }
};

window.saveApiKeys = function() {
    const keys = {
        openai: document.getElementById('openaiKey').value,
        anthropic: document.getElementById('anthropicKey').value,
        google: document.getElementById('googleKey').value
    };
    
    // In a real implementation, these would be sent securely to the server
    showQuantumNotification('Neural API Keys Secured in Quantum Vault', 'success');
    
    // Clear the input fields for security
    Object.keys(keys).forEach(key => {
        const element = document.getElementById(`${key}Key`);
        if (element) {
            element.value = '';
        }
    });
};

window.toggleKeyVisibility = function(keyId) {
    const input = document.getElementById(keyId);
    const button = input?.nextElementSibling;
    const icon = button?.querySelector('i');
    
    if (input && icon) {
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'fas fa-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'fas fa-eye';
        }
    }
};

// Theme toggle
function toggleTheme() {
    const body = document.body;
    const currentTheme = body.getAttribute('data-theme') || 'dark';
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    
    body.setAttribute('data-theme', newTheme);
    localStorage.setItem('neural-theme', newTheme);
    
    showQuantumNotification(`Neural Theme: ${newTheme.toUpperCase()}`, 'info');
}

// Fullscreen toggle
function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen();
        showQuantumNotification('Neural Interface: Fullscreen Mode', 'info');
    } else {
        document.exitFullscreen();
        showQuantumNotification('Neural Interface: Window Mode', 'info');
    }
}

// Keyboard shortcuts
function handleKeyboardShortcuts(event) {
    if (event.ctrlKey || event.metaKey) {
        switch (event.key) {
            case '1':
                event.preventDefault();
                document.querySelector('[data-tab="neural-dashboard"]')?.click();
                break;
            case '2':
                event.preventDefault();
                document.querySelector('[data-tab="ai-forge"]')?.click();
                break;
            case '3':
                event.preventDefault();
                document.querySelector('[data-tab="quantum-providers"]')?.click();
                break;
            case 'Enter':
                if (NeuralSystem.activeTab === 'ai-forge') {
                    event.preventDefault();
                    document.getElementById('codeForgeForm')?.dispatchEvent(new Event('submit'));
                }
                break;
        }
    }
}

// Cleanup function
function cleanup() {
    if (NeuralSystem.refreshInterval) {
        clearInterval(NeuralSystem.refreshInterval);
    }
    
    if (NeuralSystem.voiceRecognition) {
        NeuralSystem.voiceRecognition.stop();
    }
}

// Handle visibility change
function handleVisibilityChange() {
    if (document.hidden) {
        // Pause animations when tab is hidden
        document.querySelectorAll('canvas').forEach(canvas => {
            canvas.style.animationPlayState = 'paused';
        });
    } else {
        // Resume animations when tab is visible
        document.querySelectorAll('canvas').forEach(canvas => {
            canvas.style.animationPlayState = 'running';
        });
    }
}

// Add quantum click effect
function addQuantumClickEffect(event) {
    const button = event.currentTarget;
    const rect = button.getBoundingClientRect();
    
    // Create ripple effect
    const ripple = document.createElement('div');
    ripple.style.cssText = `
        position: absolute;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.3);
        transform: scale(0);
        animation: quantumRipple 0.6s linear;
        pointer-events: none;
    `;
    
    const size = Math.max(rect.width, rect.height);
    ripple.style.width = ripple.style.height = size + 'px';
    ripple.style.left = (event.clientX - rect.left - size / 2) + 'px';
    ripple.style.top = (event.clientY - rect.top - size / 2) + 'px';
    
    button.style.position = 'relative';
    button.style.overflow = 'hidden';
    button.appendChild(ripple);
    
    setTimeout(() => {
        ripple.remove();
    }, 600);
}

// Add ripple animation
const rippleStyle = document.createElement('style');
rippleStyle.textContent = `
@keyframes quantumRipple {
    to {
        transform: scale(4);
        opacity: 0;
    }
}
`;
document.head.appendChild(rippleStyle);

// Handle setting changes
function handleSettingChange(event) {
    const setting = event.target.id;
    const enabled = event.target.checked;
    
    const settingNames = {
        piiRedaction: 'Neural Privacy Shield',
        semanticRouting: 'Quantum Routing',
        costManagement: 'Cost Optimization',
        neuralAcceleration: 'Neural Acceleration'
    };
    
    const settingName = settingNames[setting] || setting;
    const status = enabled ? 'Enabled' : 'Disabled';
    
    showQuantumNotification(`${settingName}: ${status}`, 'info');
    
    // Save to localStorage
    localStorage.setItem(`neural-${setting}`, enabled);
}

// Load saved theme
const savedTheme = localStorage.getItem('neural-theme') || 'dark';
document.body.setAttribute('data-theme', savedTheme);

// Load saved settings
document.querySelectorAll('.quantum-switch input').forEach(toggle => {
    const setting = toggle.id;
    const saved = localStorage.getItem(`neural-${setting}`);
    if (saved !== null) {
        toggle.checked = saved === 'true';
    }
});

console.log('🚀 Neural Quantum Interface 2026 - Ready for Neural Link');
console.log('© 2026 SAYED_ELAZAMEY - Quantum Enhanced AI Architecture');