const EventEmitter = require('events');
const winston = require('winston');
const OpenAI = require('openai');
const natural = require('natural');

class ContentGeneratorAgent extends EventEmitter {
  constructor() {
    super();
    this.id = 'content-generator-agent';
    this.name = 'Content Generator Agent';
    this.type = 'content_generator';
    this.description = 'وكيل إنشاء المحتوى المتخصص في إنتاج النصوص والمحتوى التسويقي';
    this.status = 'offline';
    this.configuration = {
      tone: 'professional',
      languages: ['ar', 'en'],
      maxLength: 2000,
      creativity: 0.7,
      defaultModel: 'gpt-3.5-turbo'
    };
    
    this.openai = new OpenAI({
      apiKey: process.env.OPENAI_API_KEY
    });
    
    this.generatedContent = new Map();
    this.contentTemplates = new Map();
    this.contentHistory = [];
  }

  async initialize() {
    try {
      winston.info('🤖 Initializing Content Generator Agent...');
      
      // Load content templates
      await this.loadContentTemplates();
      
      // Initialize language models
      await this.initializeLanguageModels();
      
      this.status = 'online';
      this.emit('status:changed', 'online');
      
      winston.info('✅ Content Generator Agent initialized');
    } catch (error) {
      winston.error('❌ Failed to initialize Content Generator Agent:', error);
      this.status = 'error';
      this.emit('status:changed', 'error', { error: error.message });
      throw error;
    }
  }

  async loadContentTemplates() {
    this.contentTemplates.set('blog_post', {
      ar: {
        structure: ['مقدمة', 'نقطة رئيسية 1', 'نقطة رئيسية 2', 'خاتمة'],
        template: 'اكتب مقال عن {topic} يتضمن {key_points}. استخدم أسلوب {tone} واطول {length}.'
      },
      en: {
        structure: ['Introduction', 'Main Point 1', 'Main Point 2', 'Conclusion'],
        template: 'Write a blog post about {topic} including {key_points}. Use a {tone} tone and make it {length} words.'
      }
    });

    this.contentTemplates.set('social_media', {
      ar: {
        platforms: ['twitter', 'linkedin', 'facebook', 'instagram'],
        template: 'اكتب منشور {platform} عن {topic} بطول {length} وأسلوب {tone}. أضف هاشتاغات مناسبة: {hashtags}'
      },
      en: {
        platforms: ['twitter', 'linkedin', 'facebook', 'instagram'],
        template: 'Write a {platform} post about {topic} that is {length} and {tone} tone. Include relevant hashtags: {hashtags}'
      }
    });

    this.contentTemplates.set('email', {
      ar: {
        types: ['welcome', 'newsletter', 'promotional', 'follow_up'],
        template: 'اكتب {type} email إلى {audience} عن {topic} بأسلوب {tone} وطول {length}.'
      },
      en: {
        types: ['welcome', 'newsletter', 'promotional', 'follow_up'],
        template: 'Write a {type} email to {audience} about {topic} in a {tone} tone that is {length} words.'
      }
    });

    this.contentTemplates.set('product_description', {
      ar: {
        sections: ['المقدمة', 'الميزات', 'الفوائد', 'المواصفات'],
        template: 'اكتب وصف منتج لـ {product_name} يتضمن {features} ويفيد {benefits}. استخدم أسلوب {tone}.'
      },
      en: {
        sections: ['Introduction', 'Features', 'Benefits', 'Specifications'],
        template: 'Write a product description for {product_name} including {features} and highlighting {benefits}. Use a {tone} tone.'
      }
    });

    this.contentTemplates.set('seo_article', {
      ar: {
        structure: ['عنوان SEO', 'meta description', 'مقدمة', 'نقاط رئيسية', 'خاتمة'],
        template: 'اكتب مقال SEO عن {keyword} يتضمن {related_keywords}. العنوان يجب أن يكون {title_length} وحساب {meta_description_length} حرف للميتا.'
      },
      en: {
        structure: ['SEO Title', 'Meta Description', 'Introduction', 'Main Points', 'Conclusion'],
        template: 'Write an SEO article about {keyword} including {related_keywords}. The title should be {title_length} and meta description {meta_description_length} characters.'
      }
    });
  }

  async initializeLanguageModels() {
    // Initialize NLP tools
    this.tokenizer = new natural.WordTokenizer();
    this.stemmer = natural.PorterStemmer;
    
    // Content generation rules
    this.contentRules = {
      readability: {
        ar: {
          avgSentenceLength: 15,
          maxParagraphLength: 100,
          avoidComplexTerms: true
        },
        en: {
          avgSentenceLength: 12,
          maxParagraphLength: 150,
          avoidComplexTerms: true
        }
      },
      tone: {
        friendly: { emojis: true, contractions: true, personal: true },
        professional: { formal: true, technical: true, objective: true },
        casual: { colloquial: true, conversational: true, brief: true },
        persuasive: { emotional: true, action: true, benefits: true }
      }
    };
  }

  async processTask(taskData) {
    try {
      this.emit('task:started', taskData.id);
      
      const { type, data } = taskData;
      
      switch (type) {
        case 'generate_content':
          return await this.generateContent(data);
        case 'improve_content':
          return await this.improveContent(data);
        case 'generate_headlines':
          return await this.generateHeadlines(data);
        case 'seo_optimization':
          return await this.optimizeForSEO(data);
        case 'content_analysis':
          return await this.analyzeContent(data);
        case 'translate_content':
          return await this.translateContent(data);
        default:
          throw new Error(`Unsupported task type: ${type}`);
      }
    } catch (error) {
      this.emit('log', 'error', `Task processing failed: ${error.message}`, { taskData });
      throw error;
    } finally {
      this.emit('task:completed', taskData.id, { status: 'completed' });
    }
  }

  async generateContent(data) {
    const { 
      type, 
      topic, 
      language = 'ar', 
      tone = this.configuration.tone,
      length = 'medium',
      audience = 'general',
      specific_requirements = {},
      keywords = []
    } = data;
    
    let content = '';
    let metadata = {
      type,
      topic,
      language,
      tone,
      length,
      generated_at: new Date().toISOString()
    };
    
    try {
      // Get template for content type
      const template = this.contentTemplates.get(type);
      if (!template) {
        throw new Error(`Unknown content type: ${type}`);
      }
      
      const langTemplate = template[language] || template.en;
      const prompt = this.buildPrompt(langTemplate, {
        topic,
        tone,
        length,
        audience,
        keywords,
        ...specific_requirements
      });
      
      // Generate content using OpenAI
      if (process.env.OPENAI_API_KEY) {
        content = await this.generateWithAI(prompt, language, tone, length);
      } else {
        content = await this.generateWithTemplate(template, data);
      }
      
      // Post-process content
      content = await this.postProcessContent(content, language, tone);
      
      // Store generated content
      const contentId = `content_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      this.generatedContent.set(contentId, {
        id: contentId,
        content,
        metadata,
        keywords,
        word_count: this.countWords(content, language),
        created_at: new Date().toISOString()
      });
      
      // Emit content generated event
      this.emit('content:generated', {
        contentId,
        type,
        topic,
        language,
        length,
        word_count: this.generatedContent.get(contentId).word_count
      });
      
      return {
        content_id: contentId,
        content,
        metadata,
        word_count: this.generatedContent.get(contentId).word_count,
        reading_time: this.calculateReadingTime(content, language)
      };
      
    } catch (error) {
      this.emit('log', 'error', `Content generation failed for ${type}: ${error.message}`, { data });
      throw error;
    }
  }

  buildPrompt(template, variables) {
    let prompt = template.template || template;
    
    // Replace variables in template
    for (const [key, value] of Object.entries(variables)) {
      const placeholder = new RegExp(`{${key}}`, 'g');
      const replacement = Array.isArray(value) ? value.join(', ') : value;
      prompt = prompt.replace(placeholder, replacement);
    }
    
    return prompt;
  }

  async generateWithAI(prompt, language, tone, length) {
    const wordCount = this.getWordCountForLength(length);
    
    const messages = [
      {
        role: "system",
        content: `You are an expert content writer. Generate high-quality, engaging content in ${language} with a ${tone} tone. Make it approximately ${wordCount} words. Ensure the content is original, well-structured, and relevant to the topic.`
      },
      {
        role: "user",
        content: prompt
      }
    ];
    
    try {
      const completion = await this.openai.chat.completions.create({
        model: this.configuration.defaultModel,
        messages,
        max_tokens: Math.min(wordCount * 2, 4000), // Rough estimation
        temperature: this.configuration.creativity,
        presence_penalty: 0.1,
        frequency_penalty: 0.1
      });
      
      return completion.choices[0].message.content.trim();
    } catch (error) {
      winston.error('OpenAI content generation failed:', error);
      throw new Error(`AI content generation failed: ${error.message}`);
    }
  }

  async generateWithTemplate(template, data) {
    // Fallback template-based generation when OpenAI is not available
    const { type, topic, language, tone, length } = data;
    
    const templates = {
      blog_post: language === 'ar' 
        ? `# مقال: ${topic}\n\n## المقدمة\nهذا المقال يتناول موضوع ${topic} وسنناقش النقاط الرئيسية المتعلقة به.\n\n## النقاط الرئيسية\n1. النقطة الأولى\n2. النقطة الثانية\n3. النقطة الثالثة\n\n## الخاتمة\nفي الختام، يمكننا القول أن ${topic} له أهمية كبيرة.\n\n---\n*تم إنشاء هذا المحتوى بواسطة وكيل إنشاء المحتوى*`
        : `# Article: ${topic}\n\n## Introduction\nThis article discusses ${topic} and will cover the main points related to it.\n\n## Main Points\n1. First point\n2. Second point\n3. Third point\n\n## Conclusion\nIn conclusion, we can say that ${topic} is very important.\n\n---\n*This content was generated by the Content Generator Agent*`,
        
      social_media: language === 'ar'
        ? `🚀 ${topic}\n\nاكتشف المزيد عن ${topic} معنا! \n\n#${topic.replace(/\s+/g, '')} #محتوى #مفيد`
        : `🚀 ${topic}\n\nDiscover more about ${topic} with us!\n\n#${topic.replace(/\s+/g, '')} #content #useful`
    };
    
    return templates[type] || `Generated content about ${topic}`;
  }

  async generateHeadlines(data) {
    const { topic, language = 'ar', tone = 'engaging', count = 5, type = 'general' } = data;
    
    let headlines = [];
    
    try {
      if (process.env.OPENAI_API_KEY) {
        const prompt = `Generate ${count} catchy headlines for ${topic} in ${language} with a ${tone} tone for ${type} content. Make them engaging and compelling.`;
        
        const completion = await this.openai.chat.completions.create({
          model: "gpt-3.5-turbo",
          messages: [
            {
              role: "system",
              content: `You are an expert headline writer. Generate compelling, engaging headlines in ${language}.`
            },
            {
              role: "user",
              content: prompt
            }
          ],
          max_tokens: 300,
          temperature: 0.8
        });
        
        const generatedHeadlines = completion.choices[0].message.content
          .split('\n')
          .filter(line => line.trim())
          .map(line => line.replace(/^\d+\.\s*/, '').trim());
        
        headlines = generatedHeadlines.slice(0, count);
      } else {
        // Fallback headlines
        const headlineTemplates = {
          ar: [
            `دليل شامل عن ${topic}`,
            `كل ما تحتاج لمعرفته عن ${topic}`,
            `كيفية النجاح مع ${topic}`,
            `${topic}: أسرار النجاح`,
            `أفضل النصائح لـ ${topic}`
          ],
          en: [
            `The Complete Guide to ${topic}`,
            `Everything You Need to Know About ${topic}`,
            `How to Succeed with ${topic}`,
            `${topic}: Secrets to Success`,
            `Best Tips for ${topic}`
          ]
        };
        
        headlines = headlineTemplates[language] || headlineTemplates.en;
        headlines = headlines.slice(0, count);
      }
      
      return {
        headlines,
        metadata: {
          topic,
          language,
          tone,
          count,
          generated_at: new Date().toISOString()
        }
      };
      
    } catch (error) {
      this.emit('log', 'error', `Headlines generation failed: ${error.message}`, { data });
      throw error;
    }
  }

  async improveContent(data) {
    const { content, language = 'ar', improvements = ['clarity', 'engagement', 'seo'] } = data;
    
    try {
      let improvedContent = content;
      let feedback = [];
      
      // Analyze current content
      const analysis = await this.analyzeContent({ content, language });
      
      // Apply improvements based on analysis
      for (const improvement of improvements) {
        switch (improvement) {
          case 'clarity':
            if (analysis.readability_score < 0.7) {
              improvedContent = await this.improveClarity(improvedContent, language);
              feedback.push('Improved content clarity');
            }
            break;
            
          case 'engagement':
            if (analysis.engagement_score < 0.6) {
              improvedContent = await this.improveEngagement(improvedContent, language);
              feedback.push('Enhanced content engagement');
            }
            break;
            
          case 'seo':
            if (analysis.seo_score < 0.8) {
              const seoResult = await this.optimizeForSEO({ content: improvedContent, language });
              improvedContent = seoResult.optimized_content;
              feedback.push('Applied SEO optimizations');
            }
            break;
        }
      }
      
      return {
        original_content: content,
        improved_content: improvedContent,
        feedback,
        analysis_before: analysis,
        improvements_applied: improvements,
        word_count_change: this.countWords(improvedContent, language) - this.countWords(content, language)
      };
      
    } catch (error) {
      this.emit('log', 'error', `Content improvement failed: ${error.message}`, { data });
      throw error;
    }
  }

  async analyzeContent(data) {
    const { content, language = 'ar' } = data;
    
    try {
      const wordCount = this.countWords(content, language);
      const sentences = this.splitIntoSentences(content, language);
      const avgSentenceLength = sentences.length > 0 ? wordCount / sentences.length : 0;
      
      // Calculate readability score
      const readabilityScore = this.calculateReadabilityScore(content, language, avgSentenceLength);
      
      // Check for engagement elements
      const engagementScore = this.calculateEngagementScore(content, language);
      
      // SEO analysis
      const seoScore = this.calculateSEOScore(content, language);
      
      // Language quality
      const languageScore = this.calculateLanguageQuality(content, language);
      
      return {
        word_count: wordCount,
        sentence_count: sentences.length,
        avg_sentence_length: avgSentenceLength,
        readability_score: readabilityScore,
        engagement_score: engagementScore,
        seo_score: seoScore,
        language_score: languageScore,
        overall_score: (readabilityScore + engagementScore + seoScore + languageScore) / 4,
        language,
        analyzed_at: new Date().toISOString()
      };
      
    } catch (error) {
      this.emit('log', 'error', `Content analysis failed: ${error.message}`, { data });
      throw error;
    }
  }

  async optimizeForSEO(data) {
    const { content, target_keyword, language = 'ar', density = 0.02 } = data;
    
    try {
      let optimizedContent = content;
      const changes = [];
      
      // Calculate keyword density
      const wordCount = this.countWords(content, language);
      const keywordCount = this.countKeywordOccurrences(content, target_keyword, language);
      const currentDensity = keywordCount / wordCount;
      
      // Add keyword if density is too low
      if (currentDensity < density) {
        const keywordToAdd = Math.ceil((density * wordCount) - keywordCount);
        optimizedContent = await this.addKeywords(optimizedContent, target_keyword, keywordToAdd, language);
        changes.push(`Added ${keywordToAdd} instances of "${target_keyword}"`);
      }
      
      // Optimize headings structure
      optimizedContent = await this.optimizeHeadings(optimizedContent, target_keyword, language);
      changes.push('Optimized heading structure');
      
      // Add meta description if not present
      const metaDescription = await this.generateMetaDescription(optimizedContent, target_keyword, language);
      
      return {
        original_content: content,
        optimized_content: optimizedContent,
        meta_description: metaDescription,
        keyword_density: {
          before: currentDensity,
          after: this.countKeywordOccurrences(optimizedContent, target_keyword, language) / this.countWords(optimizedContent, language),
          target: density
        },
        changes_made: changes,
        seo_score: this.calculateSEOScore(optimizedContent, language)
      };
      
    } catch (error) {
      this.emit('log', 'error', `SEO optimization failed: ${error.message}`, { data });
      throw error;
    }
  }

  async translateContent(data) {
    const { content, target_language, source_language = 'ar' } = data;
    
    try {
      let translatedContent = content;
      
      if (process.env.OPENAI_API_KEY) {
        const completion = await this.openai.chat.completions.create({
          model: "gpt-3.5-turbo",
          messages: [
            {
              role: "system",
              content: `You are a professional translator. Translate the following text from ${source_language} to ${target_language}. Maintain the original meaning, tone, and structure.`
            },
            {
              role: "user",
              content: content
            }
          ],
          max_tokens: 4000,
          temperature: 0.3
        });
        
        translatedContent = completion.choices[0].message.content.trim();
      } else {
        throw new Error('Translation requires OpenAI API key');
      }
      
      return {
        original_content: content,
        translated_content: translatedContent,
        source_language,
        target_language,
        translation_method: 'openai_gpt3',
        translated_at: new Date().toISOString()
      };
      
    } catch (error) {
      this.emit('log', 'error', `Translation failed: ${error.message}`, { data });
      throw error;
    }
  }

  // Helper methods
  countWords(text, language) {
    if (!text || !text.trim()) return 0;
    
    const words = this.tokenizer.tokenize(text);
    return words.length;
  }

  splitIntoSentences(text, language) {
    if (!text) return [];
    
    const sentenceEndings = language === 'ar' ? /[.!?]/ : /[.!?]/;
    return text.split(sentenceEndings).filter(s => s.trim().length > 0);
  }

  calculateReadabilityScore(content, language, avgSentenceLength) {
    const config = this.contentRules.readability[language];
    const targetLength = config.avgSentenceLength;
    
    // Score based on how close to target sentence length
    const score = Math.max(0, Math.min(1, 1 - Math.abs(avgSentenceLength - targetLength) / targetLength));
    
    return Math.round(score * 100) / 100;
  }

  calculateEngagementScore(content, language) {
    let score = 0.5; // Base score
    
    // Check for engagement elements
    const engagementIndicators = {
      questions: (content.match(/[?؟]/g) || []).length,
      exclamations: (content.match(/[!¡]/g) || []).length,
      calls_to_action: language === 'ar' 
        ? (content.match(/اشترك|انضم|تابع|اكتشف|معلومات/gi) || []).length
        : (content.match(/subscribe|join|follow|discover|learn/gi) || []).length
    };
    
    const totalIndicators = Object.values(engagementIndicators).reduce((a, b) => a + b, 0);
    score = Math.min(1, 0.5 + (totalIndicators * 0.1));
    
    return Math.round(score * 100) / 100;
  }

  calculateSEOScore(content, language) {
    let score = 0.5;
    
    // Basic SEO factors
    const hasHeadings = /(^|\n)#{1,6}\s/m.test(content) || /(^|\n)[أ-يA-Za-z][^.]*$/m.test(content);
    const hasParagraphs = content.split('\n').length > 3;
    const wordCount = this.countWords(content, language);
    
    if (hasHeadings) score += 0.2;
    if (hasParagraphs) score += 0.2;
    if (wordCount > 300) score += 0.1;
    
    return Math.min(1, Math.round(score * 100) / 100);
  }

  calculateLanguageQuality(content, language) {
    // Basic language quality check
    const wordCount = this.countWords(content, language);
    const avgWordLength = content.length / wordCount;
    
    let score = 0.5;
    
    // Penalize very short or very long average word lengths
    const expectedAvgLength = language === 'ar' ? 4 : 5;
    const lengthDiff = Math.abs(avgWordLength - expectedAvgLength);
    
    if (lengthDiff < 1) score += 0.3;
    else if (lengthDiff < 2) score += 0.1;
    
    return Math.min(1, Math.round(score * 100) / 100);
  }

  getWordCountForLength(length) {
    const lengthMap = {
      short: 150,
      medium: 500,
      long: 1000,
      very_long: 2000
    };
    
    return lengthMap[length] || lengthMap.medium;
  }

  async postProcessContent(content, language, tone) {
    let processedContent = content;
    
    // Remove extra whitespace
    processedContent = processedContent.replace(/\n\s*\n\s*\n/g, '\n\n');
    
    // Ensure proper spacing
    processedContent = processedContent.replace(/\s+/g, ' ');
    processedContent = processedContent.replace(/\n\s+/g, '\n');
    
    // Add proper punctuation if missing
    if (language === 'ar' && !processedContent.endsWith('.')) {
      processedContent = processedContent.trim() + '।';
    } else if (language === 'en' && !processedContent.endsWith('.')) {
      processedContent = processedContent.trim() + '.';
    }
    
    return processedContent;
  }

  async improveClarity(content, language) {
    // Simple clarity improvements
    let improved = content;
    
    // Split long sentences
    const sentences = this.splitIntoSentences(improved, language);
    const shortSentences = sentences.map(sentence => {
      if (sentence.split(' ').length > 20) {
        // Split at conjunctions
        const conjunctions = language === 'ar' ? ['و', 'لكن', 'لأن'] : ['and', 'but', 'because'];
        for (const conj of conjunctions) {
          const parts = sentence.split(conj);
          if (parts.length > 1) {
            return parts.join(conj) + '. ' + parts.slice(1).join(conj);
          }
        }
      }
      return sentence;
    });
    
    return shortSentences.join(' ');
  }

  async improveEngagement(content, language) {
    let improved = content;
    
    // Add questions to engage readers
    const engagementQuestions = language === 'ar' 
      ? ['هل تعلم أن...؟', 'هل فكرت يوماً في...؟', 'ماذا لو...؟']
      : ['Did you know that...?', 'Have you ever thought about...?', 'What if...?'];
    
    // Add a rhetorical question after introduction
    const paragraphs = improved.split('\n\n');
    if (paragraphs.length > 1) {
      const randomQuestion = engagementQuestions[Math.floor(Math.random() * engagementQuestions.length)];
      paragraphs.splice(1, 0, randomQuestion);
      improved = paragraphs.join('\n\n');
    }
    
    return improved;
  }

  calculateReadingTime(content, language) {
    const wordsPerMinute = language === 'ar' ? 180 : 200;
    const words = this.countWords(content, language);
    const minutes = Math.ceil(words / wordsPerMinute);
    return minutes;
  }

  // Health check
  async healthCheck() {
    return {
      healthy: true,
      status: this.status,
      generated_content_count: this.generatedContent.size,
      uptime: Date.now() - this.startTime,
      lastActivity: new Date().toISOString()
    };
  }

  // Configuration
  getConfiguration() {
    return this.configuration;
  }

  setConfiguration(config) {
    this.configuration = { ...this.configuration, ...config };
  }

  // Status management
  async setStatus(newStatus) {
    this.status = newStatus;
    this.emit('status:changed', newStatus);
  }

  async getStatus() {
    return this.status;
  }

  // Metrics collection
  async collectMetrics() {
    return {
      content_generated: {
        value: this.generatedContent.size,
        type: 'counter',
        description: 'Total content pieces generated'
      },
      avg_word_count: {
        value: Array.from(this.generatedContent.values())
          .reduce((sum, content) => sum + content.word_count, 0) / Math.max(this.generatedContent.size, 1),
        type: 'gauge',
        description: 'Average word count of generated content'
      },
      languages_supported: {
        value: this.configuration.languages.length,
        type: 'gauge',
        description: 'Number of supported languages'
      }
    };
  }

  async canAcceptTasks() {
    return this.status === 'online';
  }

  needsTaskQueue() {
    return true;
  }

  getTaskQueueConfig() {
    return {
      defaultJobOptions: {
        attempts: 2,
        backoff: {
          type: 'exponential',
          delay: 2000,
        },
      }
    };
  }

  getId() {
    return this.id;
  }

  getName() {
    return this.name;
  }

  getType() {
    return this.type;
  }

  getDescription() {
    return this.description;
  }

  getCapabilities() {
    return [
      'content_generation',
      'seo_optimization',
      'translation',
      'headlines',
      'content_improvement',
      'multilingual_support'
    ];
  }

  getVersion() {
    return '1.0.0';
  }

  async stop() {
    this.status = 'offline';
    this.emit('status:changed', 'offline');
    winston.info('🛑 Content Generator Agent stopped');
  }
}

module.exports = ContentGeneratorAgent;