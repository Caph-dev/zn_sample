/**
 * Geist Theme — Vercel-style refit of the neutral theme.
 *
 * The redesign brief: 简约 / Vercel 风格. Monochrome surfaces, a hairline
 * #eaeaea border, and one blue (#0070f3) used for every action and status
 * accent — the black-primary variant was dropped, so buttons, checkboxes,
 * switches and links all read as the same blue.
 *
 * This theme extends the neutral theme (which owns the OKLCH categorical
 * status ramps and the component overrides), then re-tokens the core —
 * surfaces, accent, text, borders, focus, radius — to the
 * Vercel/Geist palette. The categorical hues are left untouched: their
 * pastel-light / tinted-dark structure already fits the restrained look.
 *
 * All free-standing values live here in the theme (per the repo rule that
 * brand/accent belongs in the theme, never in :root overrides).
 * Font files are self-hosted Geist woff2 (see /static/fonts and the
 * @font-face rules in assistant/web/static/console-shell.css).
 */

import {defineTheme} from '@astryxdesign/core/theme';
import {neutralTheme} from '../neutral/neutralTheme';

export const geistTheme = defineTheme({
  name: 'geist',

  extends: neutralTheme,

  // Scale configs REPLACE the base's rather than merging, so re-declare
  // them explicitly with the values this theme wants.
  typography: {
    scale: {base: 14, ratio: 1.2},
    body: {
      family: 'Geist',
      fallbacks:
        '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    },
    heading: {
      family: 'Geist',
      fallbacks:
        '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
      weights: {
        1: 'semibold',
        2: 'semibold',
        3: 'semibold',
        4: 'semibold',
      },
    },
    code: {
      family: 'Geist Mono',
      fallbacks:
        'ui-monospace, "SF Mono", Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
    },
  },

  motion: {fast: 125, medium: 300, slow: 700, ratio: 0.75},

  tokens: {
    // =========================================================================
    // Backgrounds — Vercel console: white canvas, everything else grouped by
    // hairline borders and #fafafa washes instead of tone steps. Dark mode
    // goes near-black like the Vercel dashboard (body #000, card #111).
    '--color-background-surface': ['#ffffff', '#111111'],
    '--color-background-body': ['#ffffff', '#000000'],
    '--color-background-card': ['#ffffff', '#111111'],
    '--color-background-popover': ['#ffffff', '#111111'],
    '--color-background-muted': ['#fafafa', '#1b1b1b'],
    '--color-background-inverted': ['#000000', '#000000'],

    // =========================================================================
    // Accent — 蓝色主色（#0070f3）。原稿是「黑主色 + 蓝色只留给链接/状态」，
    // 现改为整个主色即主题自带的 Geist 蓝，按钮/勾选/开关/进度条同色。
    // 三级色阶取自主题已有蓝色：常态 #0070f3 → hover #0060be → pressed #0057ad。
    '--color-accent': ['#0070f3', '#3291ff'],
    '--color-accent-hover': ['#0060be', '#5aa7ff'],
    '--color-accent-pressed': ['#0057ad', '#3291ff'],
    '--color-accent-muted': ['#e5f1fd', '#9eb7ff3D'],
    '--color-neutral': ['#fafafa', '#FFFFFF1A'],

    // Overlays — neutral black/white washes (no more purple tint on hover).
    '--color-overlay': ['#00000066', '#000000CC'],
    '--color-overlay-hover': ['#0000000A', '#FFFFFF0D'],
    '--color-overlay-pressed': ['#00000014', '#FFFFFF1A'],

    // Text — black on white (#000), secondary #666 (Geist gray), disabled
    // #999. Links flip to the Vercel blue so the single accent reads clearly.
    '--color-text-primary': ['#000000', '#ededed'],
    '--color-text-secondary': ['#666666', '#888888'],
    '--color-text-disabled': ['#999999', '#555555'],
    '--color-text-accent': ['#0070f3', '#3291ff'],
    '--color-on-dark': '#ffffff',
    '--color-on-light': '#000000',
    '--color-on-accent': ['#ffffff', '#000000'],
    '--color-on-success': ['#ffffff', '#000000'],
    '--color-on-error': ['#ffffff', '#000000'],
    '--color-on-warning': '#000000',

    // Icons
    '--color-icon-accent': ['#0070f3', '#3291ff'],
    '--color-icon-primary': ['#000000', '#ededed'],
    '--color-icon-secondary': ['#666666', '#888888'],
    '--color-icon-disabled': ['#999999', '#555555'],

    // Status text/muted pairs stay the neutral AA-tuned values; they already
    // read as calm pastel chips and fit the monochrome frame.

    // Borders — the Vercel signature hairline #eaeaea; emphasized sits one
    // step darker for inputs and secondary buttons that need definition.
    '--color-border': ['#eaeaea', '#333333'],
    '--color-border-emphasized': ['#d4d4d4', '#525252'],
    '--color-skeleton': ['#ededed', '#525252'],

    // Categorical blue — retuned to the Geist blue so "info/blue" statuses
    // match the link accent. Other categorical hues keep the neutral values.
    '--color-background-blue': ['#e5f1fd', '#9eb7ff3D'],
    '--color-border-blue': ['#b9d7fa', '#6d9cfe'],
    '--color-icon-blue': ['#0060be', '#9eb7ff'],
    '--color-text-blue': ['#0057ad', '#c7d3ff'],

    // Focus — Geist-style blue ring on input focus and keyboard outlines.
    '--focus-outline-color': ['#0070f3', '#3291ff'],

    // Radius — Geist shape: 4/6/8/12 instead of the softer 4/8/12/16.
    '--radius-inner': '0.25rem',
    '--radius-element': '0.375rem',
    '--radius-container': '0.5rem',
    '--radius-page': '0.75rem',

    // Shadows — the extended neutral theme's inset rings still tint with the
    // old purple accent; re-root them on the Geist blue so selected/hovered
    // channels match the single-accent rule.
    '--shadow-inset-hover': 'inset 0px 0px 0px 1px #0070f355',
    '--shadow-inset-selected': 'inset 0px 0px 0px 1px #0070f380',
  },

  components: {
    // The extended neutral theme centers the Info badge/StatusDot/progressbar
    // accent on #0074e2; snap them to the Vercel blue so every blue in the
    // theme is the same hue.
    badge: {
      'variant:info': {
        backgroundColor: 'light-dark(#0070f3, #6d9cfe)',
        color: 'light-dark(#ffffff, #171717)',
      },
    },
    statusdot: {
      'variant:accent': {backgroundColor: 'light-dark(#0070f3, #6d9cfe)'},
    },
  },
});
