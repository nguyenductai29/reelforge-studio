/**
 * The frame every page of the app shell sits in: modest gutters (16 px on a phone, 20 px on a tablet, 24 px on a
 * desktop) and no width limit beside the sidebar but a cap for ultra-wide screens, so dashboards, tables and grids
 * use the room they have. Pages narrow what reads better narrow (a short form, a paragraph, a legal text) themselves.
 */
export const PAGE_FRAME = "mx-auto w-full max-w-[2400px] px-4 sm:px-5 lg:px-6";
