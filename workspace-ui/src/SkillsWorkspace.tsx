import { useMemo } from 'react';
import {
  AlertCircle,
  Layers,
  Sparkles,
  Wrench,
} from 'lucide-react';
import { StatusBadge } from './StatusBadge';
import {
  capabilityMatrix,
  nameOf,
  notesOf,
  type CapabilityStatus,
  type Language as CapLang,
} from './capabilities';
import type { ConnectionState, Language } from './integrations';

const copyByLanguage = {
  ar: {
    title: 'المهارات',
    subtitle: 'مهارات Celia: إطار التعريف موجود، والتشغيل العام لم يُوصَّل بعد',
    source: 'مصدر البيانات: capabilities.json · celia-skill-factory',
    framework: 'إطار مهارات Celia',
    frameworkNote: 'إطار تعريف المهارات (celia-skill-factory) موجود في المستودع. لا توجد مهارات جاهزة مُشغَّلة عبر الواجهة في هذه المرحلة.',
    honest: 'الصدق الهندسي',
    honestNote: 'كل مهارة تحمل شارة حالة صريحة. المهارات غير المكتملة لا تملك زر "تشغيل" يوحي بقدرة متصلة.',
    noRun: 'بلا تشغيل تلقائي في هذه المرحلة',
    plannedHint: 'مخطّطة — لم تُبنَ بعد.',
    blockedHint: 'محظورة في هذه المرحلة.',
    unavailableHint: 'غير متاحة حاليًا.',
    partialHint: 'موجودة جزئيًا — بعض المسارات متصلة.',
    implementedHint: 'منفّذة فعليًا ومتاحة.',
  },
  en: {
    title: 'Skills',
    subtitle: 'Celia skills: the definition framework exists; general execution is not wired yet',
    source: 'Data source: capabilities.json · celia-skill-factory',
    framework: 'Celia skill framework',
    frameworkNote: 'The skill definition framework (celia-skill-factory) exists in the repo. No ready skills are executed through the UI in this phase.',
    honest: 'Engineering honesty',
    honestNote: 'Every skill carries an explicit status badge. Incomplete skills have no "run" button implying a connected capability.',
    noRun: 'No automatic run in this phase',
    plannedHint: 'Planned — not built yet.',
    blockedHint: 'Blocked in this phase.',
    unavailableHint: 'Unavailable right now.',
    partialHint: 'Partial — some paths are connected.',
    implementedHint: 'Actually implemented and available.',
  },
} as const;

type Copy = { [Key in keyof (typeof copyByLanguage)['en']]: string };

function hintFor(status: CapabilityStatus, copy: Copy): string {
  switch (status) {
    case 'implemented': return copy.implementedHint;
    case 'partial': return copy.partialHint;
    case 'planned': return copy.plannedHint;
    case 'unavailable': return copy.unavailableHint;
    default: return copy.blockedHint;
  }
}

export function SkillsWorkspace({ lang, onConnectionChange }: { lang: Language; onConnectionChange: (state: ConnectionState) => void }) {
  const copy = copyByLanguage[lang];
  useMemo(() => onConnectionChange('online'), [onConnectionChange]);

  return (
    <div className="integration-page skills-page" dir={lang === 'ar' ? 'rtl' : 'ltr'}>
      <div className="integration-page-header">
        <div className="integration-heading">
          <div className="integration-heading-icon skills-heading-icon"><Layers size={19} /></div>
          <div>
            <div className="integration-eyebrow">{copy.source}</div>
            <h1>{copy.title}</h1>
            <p>{copy.subtitle}</p>
          </div>
        </div>
      </div>

      <section className="studio-surface skills-framework-card">
        <div className="studio-section-heading">
          <div><span className="section-kicker"><Wrench size={13} />{copy.framework}</span><h2>{copy.framework}</h2></div>
        </div>
        <p className="governance-note"><Sparkles size={13} />{copy.frameworkNote}</p>
      </section>

      <section className="capability-group">
        <div className="studio-section-heading">
          <div><span className="section-kicker"><Layers size={13} />{copy.title}</span><h2>{copy.title}</h2></div>
        </div>
        <div className="capability-card-grid">
          {capabilityMatrix.skills.map((skill) => (
            <article key={skill.id} className={`capability-card capability-status-${skill.status}`}>
              <div className="capability-card-head">
                <strong>{nameOf(skill, lang as CapLang)}</strong>
                <StatusBadge status={skill.status} lang={lang as CapLang} />
              </div>
              <p className="capability-card-notes">{notesOf(skill, lang as CapLang)}</p>
              <div className="capability-card-foot">
                <code className="capability-source">{skill.id}</code>
                <small className="capability-hint">{hintFor(skill.status, copy)}</small>
              </div>
            </article>
          ))}
        </div>
      </section>

      <div className="capability-legend">
        <AlertCircle size={13} />
        <span>{copy.honest}:</span>
        <small className="capability-hint">{copy.honestNote}</small>
        <span className="capability-total">{copy.noRun}</span>
      </div>
    </div>
  );
}
